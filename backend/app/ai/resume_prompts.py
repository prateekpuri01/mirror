"""Prompt templates and builders for AI-powered tailored resume generation."""

from app.ai.utils import employer_key

RESUME_PROMPT_VERSION = "resume_v3"


# ---------------------------------------------------------------------------
# Shared writing quality rules (used by all generation + editing prompts)
# ---------------------------------------------------------------------------

VOICE_RULES = """\
## How this should read

Write like a person explaining their own work to a smart colleague over \
coffee. The single most important property is that a real human would \
actually say this out loud.

- Active voice. Verb-led. "Built X" — not "X was built."
- One idea per sentence by default. A longer sentence is fine when it \
reads naturally — don't fracture clean prose just to obey a word count.
- Most sentences land under ~25 words; longer is fine when it breathes.
- Implied first person — drop the "I" from the start of sentences. \
"Designed X" beats "I designed X". A stray "we" or "my team" is fine \
when the work was actually collaborative and the sentence reads more \
naturally with it; never use "I/me/my".
- No trailing participial tails: ", demonstrating X", ", enabling Y", \
", achieving Z", ", resulting in W". This is the #1 tell of AI prose — \
split into a new sentence or delete.
- Vary sentence shape. If three bullets in a row open with "[Verb] \
[thing] for [stakeholder] using [tool]", rewrite at least one to show \
what was hard, what tradeoff was navigated, or what judgment was exercised.
- Verbs with judgment ("identified", "diagnosed", "reconciled", \
"surfaced", "advocated") beat generic action verbs ("built", "deployed", \
"analyzed", "reduced") when the work involved making a call.
- Standard American English: "among" not "amongst", "while" not "whilst", \
"use" not "utilise".

## Translate jargon — don't echo it

Source material often contains insider terms ("instantiated", \
"operationalized", "digital clone", "framework", "language-sensitive"). \
Don't echo them. Translate to what a smart non-specialist would \
understand on first read.

If you find yourself stacking 2+ modifiers on one noun ("language-\
sensitive simulation framework"), stop and split — each idea gets its \
own clause. The test: would a peer scanning this in 5 seconds ask \
"what does that mean?" If yes, rewrite as one or two shorter sentences \
in ordinary words.

## Concrete beats abstract — the second-biggest AI tell

The model's instinct under pressure is to reach for impressive-sounding \
abstract phrasing. This is the second-biggest AI tell after participial \
tails, and it's the family of "the prose looks fine at a glance but \
doesn't actually flow when read aloud." The principle: at every choice \
point, prefer the concrete and specific over the abstract and \
comprehensive. One concrete thing beats two abstract things. The \
family of symptoms to catch:

- **Abstract subjects.** When a sentence opens with "The work…", "The \
project…", "The contribution…", "This effort…", "The findings…", \
rewrite. The subject should be a person, a system, a dataset, a \
measurement, a team — or the implied first person. ("Validated against \
human coders" beats "The work validated against human coders".)
- **Hedged claims.** "Made it possible to X" / "could distinguish X \
from Y" / "X enough to Y" / "made X legible" — these are hedges that \
sound careful but mostly add air. Just state what was done. ("Validated \
against human coders" beats "made it possible to validate against \
human coders".)
- **Stacked abstract noun phrases.** Each abstract NP past the first \
roughly halves how many readers parse the sentence. "Methodological \
judgment paired with systems that people actually rely on" — two heavy \
abstract NPs jammed together. Pick one concrete thing instead, or \
restructure as two simple sentences.
- **Mismatched series.** Three-item comma series ("A, B, and C") only \
read as a list when items are roughly parallel in length AND grammatical \
shape. If they aren't, drop one or rewrite as coordination.
  - BAD (item lengths 2 / 2 / 9, mismatched shapes): "with attention to \
failure modes, misuse potential, and the gap between raw benchmark \
performance and real-world assurance"
  - BETTER: "with attention to failure modes and misuse potential" — or \
just commit: "by treating evaluation as a safety problem, not a \
benchmark game"
- **Meta-significance commentary.** "The work mattered because X", \
"This contribution matters for Y work because Z", "The project \
connected X with Y rather than Z" — telling the reader what to \
conclude instead of letting the work speak. Make the claim directly, \
then stop.
- **Defensive intensification.** This is the family of "I know this \
might sound generic so let me intensify it." Two specific symptoms:
  - "Actually" / "really" / "just" as intensifiers — "AI assistance \
they could *actually* trust", "researchers *actually* rely on", \
"models that *really* hold up". Drop the adverb; if the claim isn't \
strong without it, the claim isn't strong with it either.
  - "Rather than X" / "instead of Y" / "not just Z" tails where the \
contrast item isn't on the same dimension as the main claim. The model \
appends these to add force, but they read as defensive padding when \
they don't parallel.
  - BAD (capability claim contrasted with an opacity claim — different \
dimensions): "AI assistance they could actually trust for coding and \
synthesis, rather than another black-box text tool"
  - GOOD (both sides describe what the AI is doing on the same \
dimension): "a system that could be trusted to handle real coding work, \
not just generate plausible labels"
  - The test: read the contrast tail without the main clause. If it \
describes the same kind of thing the main clause describes, keep it. \
If it describes a different kind of thing, drop it or restructure.
- **Purpose-clause openers** ("Built X to give Y Z", "Built X to make \
Y possible") are clunkier than relative-clause openers ("Built X that \
does Y") for the same content. Prefer the relative clause.

The one-line test: read the sentence aloud. If it sounds like a press \
release, a grant abstract, or a teacher explaining significance — \
rewrite it as something a person would actually say to a colleague \
describing what they did. The fix is almost always: concrete subject, \
named verb, specific object, no air.
"""

# Back-compat aliases. The two old constants used to be embedded
# separately into ~6 prompt sites; the consolidated content now lives
# in VOICE_RULES. We keep WRITING_QUALITY_RULES as an alias and reduce
# NO_BUZZWORD_RULES to empty so existing call sites stay working
# without duplicating content. Prefer VOICE_RULES in new code.
WRITING_QUALITY_RULES = VOICE_RULES
NO_BUZZWORD_RULES = ""


# ===========================================================================
# SINGLE-SHOT PIPELINE PROMPTS (v3)
# ===========================================================================


def build_single_shot_system(profile_data: dict, memory_text: str = "") -> str:
    """Build the system prompt for single-shot resume generation.

    Guided by a strategic plan (produced in a prior LLM call), writes the entire
    resume in one pass. Uses per-bullet object schema for atomic linkage.
    """
    work_history = profile_data.get("work_history", [])

    # Build available employers list (LLM picks which to include)
    available_employers = []
    for wh in work_history:
        key = employer_key(wh["employer"])
        available_employers.append(f'- {wh["employer"]} (key: "{key}")')
    employer_list = (
        "\n".join(available_employers) if available_employers else "- See work history in profile"
    )

    # Example schema (not prescriptive — LLM picks which employers)
    experience_schema = '    "employer_key": {\n      "bullets": [{"text": "...", "accomplishment_ids": ["..."]}]\n    }'

    # Skills whitelist
    skills = profile_data.get("skills", {})
    all_skills = []
    for cat in ["technical", "communication", "tools"]:
        cat_skills = skills.get(cat, [])
        if isinstance(cat_skills, list):
            all_skills.extend(cat_skills)
        elif isinstance(cat_skills, str):
            all_skills.append(cat_skills)
    skills_whitelist = ", ".join(all_skills) if all_skills else "see profile"

    return f"""\
You are an expert resume writer. You receive a strategic plan and a full candidate \
profile. Write the complete resume in one pass — all sections, all prose, all at once.

## ABSOLUTE RULES
1. ONLY use facts from the provided profile. NEVER fabricate metrics, skills, or achievements.
2. For technical_skills: ONLY use skills from this whitelist: {skills_whitelist}. \
Only include skills that appear in or are clearly implied by the resume content you write.
3. Each sentence must be grammatically complete and parse independently.

{WRITING_QUALITY_RULES}

{memory_text}

## SECTION SPECS

### tagline
3-4 keywords separated by " · " mirroring the target job's domain language.

### summary
2-4 sentences, 50-80 words. Answers: "why should this person get an interview for THIS role?"

Rules:
- Do NOT start with "Research scientist with N years..."
- No metrics, no company names, no capability lists, no disclaimers.
- Each sentence must parse independently. Do not merge a career claim and a credential \
(e.g., clearance) into one sentence.
- Should make an ARGUMENT, not list evidence.

Good: "Builds AI tools that domain experts actually adopt — the last one replaced manual \
research workflows across a 2,000-person organization and matched expert-level accuracy."

Bad: "Research scientist with 7 years of post-PhD experience building and evaluating \
applied AI systems, NLP pipelines, and evaluation frameworks."

### selected_research
Exactly 3 accomplishments. For each, write 2-3 sentences (75-100 words):
- Sentence 1: the transformation — what's different now because this work exists.
- Sentence 2-3: technical approach and validation — enough for a technical reader to respect it.
- Do NOT vary opening verbs — each description MUST start with a different word.
- Do NOT copy impact_summary from the catalog. Rewrite completely.
- Do NOT repeat content that appears in experience bullets.

Good: "Replaced RAND's manual qualitative coding workflow — where researchers spent days \
tagging interview transcripts by hand — with an AI-assisted system that handles structured \
coding, thematic synthesis, and document Q&A. Validated against trained human coders before \
production rollout, reaching agreement rates that cleared the organization's bar for \
replacing manual processes. Now used by 400+ researchers across 600+ active projects."

Bad: "Built an LLM system for coding, synthesis, and Q&A. Tuned annotation quality. \
Checked performance against baselines." (staccato fragments, not prose)

### experience
Pick the 2-4 most relevant employers from the candidate's work history. Do NOT include \
every employer — only those with accomplishments that strengthen the case for THIS role. \
Give the most relevant employer 4-5 bullets, the next 2-3, and any others 2-3.

Available employers (use the "key" as the JSON key):
{employer_list}

Each bullet is an object with "text" and "accomplishment_ids". Rules for the text:
- Lead with WHAT CHANGED, not what you built. Transformation-led > metric-led > activity-led.
- One accomplishment per bullet. One idea per sentence.
- Translate jargon metrics: "Cohen's kappa = 0.71" → "matched trained human coders (kappa 0.71)".
- Max 25 words per sentence. 1-2 lines.
- If an accomplishment also appears in selected_research, the bullet MUST say something \
COMPLETELY DIFFERENT. Research = transformation story. Bullet = punchy outcome.

Good: "Caught securities fraud 5X faster by converting trading time series into images \
and applying recommendation algorithms — replacing a rule-based system at a major regulator."

Bad: "Processed 10 TB+ of time-series financial data through AWS pipelines to train \
neural networks for securities fraud detection." (input-led, no outcome)

### publications
2-3 for industry roles, up to 5 for research roles. Only include publications the hiring \
manager for THIS role would actually care about.

### technical_skills
3 categories: ai_systems, data_science, engineering. Lead with job-relevant skills. \
Only include skills with evidence in the resume content.

### awards
Single line, most relevant awards separated by " · ".

## OUTPUT FORMAT
Respond with ONLY valid JSON (no markdown fences):
{{
  "tagline": "...",
  "summary": "...",
  "selected_research": [
    {{
      "category_label": "2-4 WORD LABEL",
      "title": "accomplishment title (exactly as in catalog)",
      "description": "2-3 sentences, 75-100 words",
      "accomplishment_id": "id-from-catalog"
    }}
  ],
  "experience": {{
{experience_schema}
  }},
  "publications": [
    {{"citation": "Authors. Title. Venue, Year.", "publication_id": "pub-id"}}
  ],
  "technical_skills": {{
    "ai_systems": "...",
    "data_science": "...",
    "engineering": "..."
  }},
  "awards": "...",
  "_reasoning": "Brief explanation of your tailoring strategy and key decisions"
}}\
"""


def build_single_shot_prompt(
    plan_text: str,
    profile_text: str,
    job_text: str,
    company_research: dict | None = None,
    score_rationale: dict | None = None,
) -> list[dict]:
    """Build user message for single-shot resume generation."""
    parts = [
        "## Strategic Plan (follow this approach)\n",
        plan_text,
        "\n\n---\n",
        "## Candidate Profile & Accomplishments\n",
        profile_text,
        "\n---\n",
        "## Target Job Posting\n",
        job_text,
    ]

    if company_research:
        from app.ai.company_research import format_research_for_generator

        company_name = company_research.get("query_company", "the company")
        parts.append(f"\n\n{format_research_for_generator(company_research, company_name)}")

    if score_rationale:
        role_fit = score_rationale.get("role_fit", {})
        parts.append("\n\n## AI Scoring Context")
        hard_skills = role_fit.get("hard_skills", {})
        if hard_skills.get("matches"):
            parts.append(f"Skill matches: {', '.join(hard_skills['matches'])}")
        if hard_skills.get("gaps"):
            parts.append(f"Skill gaps: {', '.join(hard_skills['gaps'])}")

    parts.append("""

---

Generate the complete tailored resume as valid JSON. Follow the strategic plan's \
accomplishment selections and framing guidance. Write ALL prose at once so the sections \
read as a coherent whole — no redundancy between selected_research and experience bullets.""")

    return [{"role": "user", "content": "\n".join(parts)}]


# ===========================================================================
# SEQUENTIAL PIPELINE PROMPTS (v2 — kept for reference, no longer primary)
# ===========================================================================


# ---------------------------------------------------------------------------
# Phase 1: Strategic Plan
# ---------------------------------------------------------------------------

STRATEGIC_PLAN_SYSTEM = """\
You are a resume strategist. Given a candidate's full profile and a target job, \
produce a structured plan for building the strongest possible resume.

This plan is INTERNAL — it will guide downstream generation steps but never appear \
in the resume itself. Be analytical and specific.

## Your task
1. Identify the single strongest argument for why this candidate should get an interview.
1b. Classify the role archetype from the job posting:
   - "builder" — IC shipping code/systems
   - "customer-facing" — success engineering, solutions, support, onboarding
   - "research" — publishing, experimentation, evaluation
   - "leadership" — managing people/programs
   - "hybrid" — mix of the above
   For customer-facing roles, prioritize accomplishments showing user onboarding, adoption \
driving, stakeholder communication, training, and support. For builder roles, prioritize \
shipping velocity and technical depth. For research roles, prioritize publications and \
experimental rigor.
1c. Identify the target company's products, APIs, and tech stack from the job posting. \
If the candidate has used these products or tools (even in adjacent work), plan to \
foreground that experience. De-emphasize mentions of direct competitor products unless \
showing breadth is strategically valuable.
1d. Identify the role lane — what kind of work the team is really hiring for. Pick exactly one:
   - "evals_safety" — AI evaluation, model behavior analysis, safety, governance, red-team
   - "human_data" — annotation, expert feedback, RLHF, data quality, labeling workflows
   - "applied_ai_eng" — applied AI/ML eng, LLM apps, agentic workflows, integration
   - "research_engineer" — research engineering, infrastructure for ML research, eval infra
   - "generalist" — none of the above is a clear fit
   The lane shapes the Selected-Research section heading and the summary's framing — \
pick the one a hiring manager would say describes the team most accurately, not the \
broadest fit.
2. Pick exactly 3 accomplishments for the Selected Research section. For each, explain:
   - WHY this accomplishment (what job requirement does it address?)
   - HOW to frame the description (the angle, not the text)
   - What to RESERVE for the experience bullets (so research and bullets are complementary)
3. Pick which 2-4 employers to include (only the most relevant for THIS role — skip employers \
whose work doesn't strengthen the case). Map accomplishments to experience bullets for each \
selected employer. Specify which accomplishments to use and what angle to take.
4. Identify redundancy traps — accomplishments that appear in multiple sections — and plan \
how to differentiate them.
5. Note pitfalls to avoid (gaps not to mention, jargon to translate, etc.)

## Output
Respond with ONLY valid JSON (no markdown fences) using this exact schema:
{
  "role_archetype": "builder | customer-facing | research | leadership | hybrid",
  "role_lane": "evals_safety | human_data | applied_ai_eng | research_engineer | generalist",
  "company_stack_overlap": ["product/tool the candidate has used that matches target company"],
  "core_argument": "1-2 sentences: why this person gets an interview for THIS role",
  "selected_accomplishments": [
    {
      "accomplishment_id": "id-from-catalog",
      "rationale": "why this accomplishment for this job",
      "framing_angle": "how the research description should be framed",
      "reserve_for_bullets": "what NOT to say in the description — save for bullets"
    }
  ],
  "bullet_mapping": [
    {
      "employer_key": "employer_key",
      "accomplishment_ids": ["id1", "id2"],
      "rationale": "why these accomplishments for this employer's section",
      "emphasis": "what angle to take for this employer's bullets"
    }
  ],
  "summary_angle": "framing direction for the summary (NOT the summary text itself)",
  "tone": "technical builder | research leader | shipping velocity | etc.",
  "redundancy_warnings": [
    {
      "accomplishment_id": "id",
      "appears_in": ["selected_research.0", "experience.rand.bullets.1"],
      "differentiation": "how to make these two appearances complementary"
    }
  ],
  "pitfalls": ["gap not to mention", "jargon to translate", "etc."]
}\
"""


def build_compact_profile_for_plan(data: dict) -> str:
    """Build a compact profile for the strategic plan step.

    Unlike build_full_profile_for_resume (which sends everything),
    this sends IDs, titles, employers, key metrics, and skills —
    enough to PICK accomplishments, but not enough to WRITE descriptions.
    """
    lines = []
    personal = data.get("personal", {})
    lines.append(f"Name: {personal.get('name', 'Unknown')}")
    lines.append(f"Experience: {data.get('experience_years', 'N/A')} years post-PhD")

    # Target roles
    for r in data.get("target_roles", []):
        lines.append(f"Target: {r.get('title', '')} ({r.get('seniority', '')})")

    # Skills (compact)
    skills = data.get("skills", {})
    if skills.get("technical"):
        tech = (
            skills["technical"] if isinstance(skills["technical"], list) else [skills["technical"]]
        )
        lines.append(f"Technical skills: {', '.join(tech[:15])}")

    # Work history
    lines.append("\nWork history:")
    for wh in data.get("work_history", []):
        end = wh.get("end") or "present"
        lines.append(f"  - {wh['title']} at {wh['employer']} ({wh['start']}–{end})")
        lines.append(f"    key: {employer_key(wh['employer'])}")

    # Accomplishments — compact: ID, title, employer, key metrics, skills
    complete = data.get("complete_profile", {})
    accomplishments = complete.get("accomplishments", [])
    if accomplishments:
        lines.append(f"\nAccomplishments ({len(accomplishments)} total):")
        for a in accomplishments:
            lines.append(f"  ID: {a.get('id', '?')}")
            lines.append(
                f"  Employer: {a.get('employer', '?')} (key: {a.get('work_history_key', '?')})"
            )
            lines.append(f"  Title: {a.get('title', '?')}")
            quants = a.get("quantitative_specifics", [])
            if quants:
                lines.append(f"  Metrics: {'; '.join(quants[:3])}")
            skills_demo = a.get("skills_demonstrated", [])
            if skills_demo:
                lines.append(f"  Skills: {', '.join(skills_demo[:6])}")
            lines.append("")

    # Publications — just IDs and titles
    publications = complete.get("publications", [])
    if publications:
        lines.append(f"Publications ({len(publications)} total):")
        for p in publications:
            pid = p.get("id", "?")
            fa = " [FIRST AUTHOR]" if p.get("first_author") else ""
            lines.append(
                f"  {pid}: {p.get('title', '?')} ({p.get('venue', '?')}, {p.get('year', '?')}){fa}"
            )

    return "\n".join(lines)


def build_strategic_plan_prompt(
    profile_text: str,
    job_text: str,
    company_research: dict | None = None,
    score_rationale: dict | None = None,
    profile_data: dict | None = None,
) -> list[dict]:
    """Build user message for the strategic planning step."""
    parts = [
        "## Candidate Profile & Accomplishments\n",
        profile_text,
        "\n---\n",
        "## Target Job Posting\n",
        job_text,
    ]

    if company_research:
        from app.ai.company_research import format_research_for_generator

        company_name = company_research.get("query_company", "the company")
        parts.append(f"\n\n{format_research_for_generator(company_research, company_name)}")

    if score_rationale:
        role_fit = score_rationale.get("role_fit", {})
        score_rationale.get("interest_fit", {})
        parts.append("\n\n## AI Scoring Context")
        hard_skills = role_fit.get("hard_skills", {})
        if hard_skills.get("matches"):
            parts.append(f"Skill matches: {', '.join(hard_skills['matches'])}")
        if hard_skills.get("gaps"):
            parts.append(f"Skill gaps: {', '.join(hard_skills['gaps'])}")

    parts.append("""

---

Produce the strategic plan as valid JSON. Remember:
- Pick accomplishments that MAXIMIZE job requirement coverage — not just the most impressive ones
- Plan complementary framing between research descriptions and bullets for shared accomplishments
- The summary_angle should be a framing DIRECTION, not the summary text itself""")

    return [{"role": "user", "content": "\n".join(parts)}]


# ---------------------------------------------------------------------------
# Phase 2a: Research Picks
# ---------------------------------------------------------------------------

RESEARCH_PICKS_SYSTEM = """\
You validate and finalize which 3 accomplishments to highlight in the resume's \
Selected Research section.

You receive a strategic plan (which already picked 3) and the full accomplishment catalog. \
Confirm or adjust the picks based on the full catalog.

Output ONLY valid JSON (no markdown fences):
{
  "picks": [
    {
      "accomplishment_id": "id",
      "rationale": "brief reason this is the right choice"
    }
  ]
}\
"""


def build_research_picks_prompt(
    plan_text: str,
    profile_text: str,
    job_text: str,
) -> list[dict]:
    """Build user message for research accomplishment selection."""
    return [
        {
            "role": "user",
            "content": f"""\
## Strategic Plan
{plan_text}

## Full Accomplishment Catalog
{profile_text}

## Target Job
{job_text}

---

Confirm or adjust the 3 accomplishment picks from the plan. Output valid JSON.""",
        }
    ]


# ---------------------------------------------------------------------------
# Phase 2b: Research Description (one at a time)
# ---------------------------------------------------------------------------

RESEARCH_DESC_SYSTEM = """\
You write a single Selected Research entry for a tailored resume.

Write a short paragraph (75-100 words, 2-3 sentences) that sounds like a confident \
person describing their most interesting work to a smart colleague. The #1 priority \
is that it sounds NATURAL — something a human would actually write about their own work.

## Ground rules
- Only use facts from the provided accomplishment. Never fabricate.
- 75-100 words. Under 70 words will be rejected — add technical detail or impact.
- No pronouns (implied first person).
- Check the generation log — don't repeat content from prior descriptions.
- Check the plan's "reserve_for_bullets" — save that content for bullets.

## What good descriptions sound like
"Replaced RAND's manual qualitative coding workflow — where researchers spent days \
tagging interview transcripts by hand — with an AI-assisted system that handles \
structured coding, thematic synthesis, and document Q&A. Validated against trained \
human coders before production rollout, reaching agreement rates that cleared the \
organization's bar for replacing manual processes. Now used by 400+ researchers \
across 600+ active projects."

Notice: it reads like a paragraph, not a list. Each sentence flows into the next. \
The first sentence says what changed. The rest earn the reader's trust with specifics.

## What bad descriptions sound like
"Scaled technology scouting to 4.5M model decisions by classifying 300,000 documents \
across 15 categories." — Inflated metric, robotic phrasing.

"Built an LLM system for coding, synthesis, and Q&A. Tuned annotation quality. \
Checked performance against baselines." — Staccato fragments, not prose.

## Academic work for non-academic jobs
Emphasize transferable skills (experimental design, pipeline building, statistical \
analysis, image processing) over domain jargon. Mention publications as credibility \
closers. If the job IS in the same domain, lead with domain results.

## Output
Respond with ONLY valid JSON (no markdown fences):
{
  "category_label": "2-4 WORD LABEL (job-relevant, not internal framing)",
  "title": "accomplishment title (use exactly as provided)",
  "description": "2-3 sentences (75-100 words)",
  "accomplishment_id": "the accomplishment id",
  "reasoning": "why you framed it this way (internal, for the generation log)",
  "avoid_downstream": "what NOT to repeat in experience bullets for this accomplishment"
}\
"""


def build_research_desc_prompt(
    accomplishment: dict,
    plan_text: str,
    job_text: str,
    resume_so_far: str,
    log_text: str,
    plan_entry: dict | None = None,
    used_opening_verbs: list[str] | None = None,
) -> list[dict]:
    """Build user message for one research description."""
    # Build verb constraint instruction
    if used_opening_verbs:
        banned = ", ".join(f'"{v}"' for v in used_opening_verbs)
        used_verbs_instruction = (
            f"Previous descriptions already started with {banned}. "
            f"You MUST use a DIFFERENT opening word. Do NOT start with any of: {banned}. "
            f"Try: 'Replaced', 'Transformed', 'Showed', 'Discovered', 'Automated', "
            f"'Eliminated', 'Introduced', 'Redesigned', 'Uncovered'."
        )
    else:
        used_verbs_instruction = (
            "Do NOT start with 'Built' or 'Turned'. Use a transformation verb: "
            "'Replaced', 'Transformed', 'Showed', 'Automated', 'Eliminated', etc."
        )

    parts = ["## Accomplishment to Describe\n"]
    parts.append(f"ID: {accomplishment.get('id', '?')}")
    parts.append(f"Title: {accomplishment.get('title', '?')}")
    parts.append(f"Employer: {accomplishment.get('employer', '?')}")
    if accomplishment.get("impact_summary"):
        parts.append(f"Impact: {accomplishment['impact_summary'].strip()}")
    if accomplishment.get("quantitative_specifics"):
        parts.append(f"Metrics: {'; '.join(accomplishment['quantitative_specifics'])}")
    if accomplishment.get("so_what"):
        parts.append(f"So what: {accomplishment['so_what'].strip()}")
    if accomplishment.get("hands_on_work"):
        parts.append(f"Hands-on work: {accomplishment['hands_on_work'].strip()}")
    if accomplishment.get("skills_demonstrated"):
        parts.append(f"Skills: {', '.join(accomplishment['skills_demonstrated'])}")

    if plan_entry:
        parts.append("\n## Plan Guidance for This Accomplishment")
        parts.append(f"Framing angle: {plan_entry.get('framing_angle', '?')}")
        parts.append(
            f"Reserve for bullets: {plan_entry.get('reserve_for_bullets', 'nothing specific')}"
        )

    parts.append(f"\n## Strategic Plan\n{plan_text}")
    parts.append(f"\n## Resume So Far\n{resume_so_far}")
    parts.append(f"\n## Generation Log\n{log_text}")
    parts.append(f"\n## Target Job\n{job_text}")
    parts.append(f"""
---

Write the research description. Output valid JSON.

BEFORE YOU OUTPUT — check these three things:
1. Is it 75-100 words? (Under 70 = rejected. Count them.)
2. Does it sound like something a human would write about their own work? Read it aloud.
3. OPENING VERB: {used_verbs_instruction}""")

    return [{"role": "user", "content": "\n".join(parts)}]


# ---------------------------------------------------------------------------
# Phase 2c: Experience Bullets (one employer at a time)
# ---------------------------------------------------------------------------


def build_experience_bullets_system(profile_data: dict) -> str:
    """Build per-employer bullet generation system prompt."""
    skills = profile_data.get("skills", {})
    all_skills = []
    for cat in ["technical", "communication", "tools"]:
        cat_skills = skills.get(cat, [])
        if isinstance(cat_skills, list):
            all_skills.extend(cat_skills)
    skills_whitelist = ", ".join(all_skills) if all_skills else "see profile"

    return f"""\
You write experience bullets for ONE employer in a tailored resume.

Write like a human describing their own work to a smart colleague. The #1 priority is \
that every bullet sounds natural — something a real person would actually say. Do not \
contort sentences to satisfy formatting rules. If a simple, clear sentence breaks a \
"rule," keep the simple sentence.

## Ground rules
- Only use facts from the provided accomplishments. Never fabricate metrics or skills.
- Skills whitelist: {skills_whitelist}
- If an accomplishment also appears in Selected Research, say something COMPLETELY DIFFERENT \
here. The research description tells the transformation story; your bullet states the punchy \
outcome or a different angle entirely. If the prompt includes "ALREADY COVERED" notes for \
specific accomplishments, follow them exactly — they tell you what the research description \
already said so you can avoid repeating it.
- No pronouns (implied first person). No "I", "my", "we".
- Each bullet = one project/accomplishment. A bullet can be 1-3 sentences — that's fine. \
Do NOT split one project across two separate bullets. If a project has a result and a \
method, put both in the same bullet.
- Include `accomplishment_ids` for audit trail.
- When you have few bullet slots but multiple strong accomplishments, combine them.

## What good bullets sound like
Read these examples. Match this TONE — direct, specific, no filler:

"Replaced RAND's manual qualitative coding workflow with an AI system that 400+ \
researchers now use across 600+ projects."

"Classified 300K defense documents into 15 technology categories so Air Force planners \
could track emerging training technologies across the field."

"Cut market-manipulation review workloads by 30% by adding calibrated uncertainty \
estimates to fraud alerts."

"Designed and ran atom-ion collision experiments, building the trap, laser systems, and \
timing hardware from scratch. Published first-author results in Science and Nature Chemistry."

Notice: each bullet is something a human would say out loud. No metric inflation, no \
jargon, no awkward constructions. The metric is there to show scale, not to impress.

## What bad bullets sound like
"Scaled technology scouting to 4.5M model decisions by classifying 300,000 documents \
across 15 categories." — "4.5M model decisions" is inflated (300K × 15). Nobody talks \
like this. Just say what you classified and why it mattered.

"Achieved 5X fraud discovery efficiency improvement through implementation of novel \
image-based recommendation algorithms." — robotic. A human would say "Found fraud \
patterns 5X faster by converting trading data into images."

## Academic work for industry jobs
For PhD/academic bullets targeting non-academic jobs: emphasize transferable skills \
(experimental design, pipeline building, statistical analysis, image processing, \
system integration) over domain results. Mention publications as credibility closers \
("first-author in Science"), not as the main content. If the job IS in the same \
domain, then the domain results are the point.

## Output
Respond with ONLY valid JSON (no markdown fences):
{{
  "bullets": ["bullet1", "bullet2", ...],
  "accomplishment_ids": ["id1", "id2", ...],
  "reasoning": "why you chose these accomplishments and framings (internal, for log)",
  "avoid_downstream": "what NOT to repeat in the summary or other sections"
}}\
"""


def build_experience_bullets_prompt(
    employer_key_str: str,
    employer_name: str,
    bullet_count: str,
    accomplishments: list[dict],
    plan_mapping: dict | None,
    plan_text: str,
    job_text: str,
    resume_so_far: str,
    log_text: str,
    avoid_downstream_map: dict[str, str] | None = None,
) -> list[dict]:
    """Build user message for one employer's bullets.

    avoid_downstream_map: {accomplishment_id: "what NOT to repeat"} from research
    descriptions that share accomplishments with this employer.
    """
    parts = [f"## Employer: {employer_name} (key: {employer_key_str})"]
    parts.append(f"Write {bullet_count} bullets for this employer.\n")

    parts.append("## Accomplishments Available for This Employer:")
    for a in accomplishments:
        aid = a.get("id", "?")
        parts.append(f"\n  ID: {aid}")
        parts.append(f"  Title: {a.get('title', '?')}")
        if a.get("impact_summary"):
            parts.append(f"  Impact: {a['impact_summary'].strip()[:200]}")
        if a.get("quantitative_specifics"):
            parts.append(f"  Metrics: {'; '.join(a['quantitative_specifics'])}")
        if a.get("hands_on_work"):
            parts.append(f"  Hands-on: {a['hands_on_work'].strip()[:200]}")
        # Inject anti-redundancy guidance from research descriptions
        if avoid_downstream_map and aid in avoid_downstream_map:
            parts.append(f"  ⚠️ ALREADY COVERED in Selected Research: {avoid_downstream_map[aid]}")
            parts.append("  → Your bullet for this accomplishment MUST take a different angle.")

    if plan_mapping:
        parts.append(f"\n## Plan Guidance for {employer_name}")
        parts.append(f"Emphasis: {plan_mapping.get('emphasis', '?')}")
        parts.append(
            f"Planned accomplishment IDs: {', '.join(plan_mapping.get('accomplishment_ids', []))}"
        )

    parts.append(f"\n## Strategic Plan\n{plan_text}")
    parts.append(f"\n## Resume So Far (research descriptions + prior employers)\n{resume_so_far}")
    parts.append(f"\n## Generation Log\n{log_text}")
    parts.append(f"\n## Target Job\n{job_text}")
    parts.append(f"""
---

Write {bullet_count} bullets. Output valid JSON.

BEFORE YOU OUTPUT — read each bullet aloud. Does it sound like something a person \
would actually say about their work? If it sounds robotic or inflated, rewrite it \
simpler. No pronouns (I, my, we).""")

    return [{"role": "user", "content": "\n".join(parts)}]


# ---------------------------------------------------------------------------
# Phase 2d: Summary (written LAST)
# ---------------------------------------------------------------------------

SUMMARY_SYSTEM = """\
You write the resume SUMMARY — the final piece, after all other content has been generated.

Write 2-4 sentences (50-80 words) that make a hiring manager think "I need to meet \
this person." Think of it as a confident colleague saying "here's why you should \
interview them." No pronouns. No company names. No metrics. No disclaimers.

## Grammar rules
- Each sentence must be grammatically complete and parse independently.
- Do NOT merge unrelated ideas into one sentence. A career claim and a credential \
(e.g., clearance, certification) are separate thoughts — give each its own sentence \
or use a clean parenthetical.
- After writing, read each sentence on its own. If it doesn't make sense in isolation, \
split or restructure it.

## What good summaries sound like
"Builds AI tools that domain experts actually adopt — the last one replaced manual \
research workflows across a 2,000-person organization and matched expert-level accuracy. \
Background spans AI evaluation, large-scale NLP, and AI governance research published in \
top-tier venues."

Notice: makes a CLAIM, backs it with one example, grounds the candidate's range.

## What bad summaries sound like
"Technical leader who turns ambiguous AI research into execution plans, evaluation \
workflows, and shipped systems." — Generic. Capability list. Could describe anyone.

"Research scientist with 7 years of post-PhD experience building and evaluating \
applied AI systems." — Most boring possible opening.

## Output
Respond with ONLY valid JSON (no markdown fences):
{
  "summary": "2-4 sentences, 50-80 words",
  "reasoning": "why you framed it this way (internal, for the generation log)"
}\
"""


def build_summary_prompt(
    plan_text: str,
    resume_so_far: str,
    log_text: str,
    job_text: str,
) -> list[dict]:
    """Build user message for summary generation (written last)."""
    return [
        {
            "role": "user",
            "content": f"""\
## Strategic Plan
{plan_text}

## Complete Resume Content (everything generated so far)
{resume_so_far}

## Generation Log (why each section was written the way it was)
{log_text}

## Target Job
{job_text}

---

Write the summary. It should synthesize the resume content into a cohesive argument \
for why this person should get an interview. Do NOT repeat specific bullets — \
the summary makes the CASE, the bullets provide the EVIDENCE.

BEFORE YOU OUTPUT — read it aloud. Does it sound like a confident colleague vouching \
for someone, or like a template? If it lists capabilities with commas ("Led X, Y, \
and Z"), rewrite as an argument. 50-80 words. No pronouns.

Output valid JSON.""",
        }
    ]


# ---------------------------------------------------------------------------
# Phase 2e: Supporting Sections (tagline + publications + skills + awards)
# ---------------------------------------------------------------------------


def build_supporting_system(profile_data: dict) -> str:
    """Build system prompt for tagline/publications/skills/awards generation."""
    skills = profile_data.get("skills", {})
    all_skills = []
    for cat in ["technical", "communication", "tools"]:
        cat_skills = skills.get(cat, [])
        if isinstance(cat_skills, list):
            all_skills.extend(cat_skills)
    skills_whitelist = ", ".join(all_skills) if all_skills else "see profile"

    return f"""\
You generate the supporting sections of a tailored resume: tagline, publications, \
technical skills, and awards.

## Section Specs

### tagline
3-4 keywords separated by " · " that mirror the target job's domain language.

### publications
Select 2-3 publications for industry/engineering roles, up to 5 for research-focused roles. \
Fewer, more relevant publications beat a longer list — only include publications the hiring \
manager for THIS role would actually care about. Prioritize first-author and topic-aligned.

### technical_skills
3 categories: ai_systems, data_science, engineering. \
Reorder within each category to lead with job-relevant skills. \
ONLY use skills from this whitelist: {skills_whitelist} \
Cross-check each skill against the experience bullets and research descriptions in the \
resume content above. Only include skills that appear in or are clearly implied by the \
resume content. If a whitelisted skill has no supporting evidence in the resume, omit it.

### awards
Single line with most relevant awards separated by " · ".

## Output
Respond with ONLY valid JSON (no markdown fences):
{{
  "tagline": "Keyword1 · Keyword2 · Keyword3",
  "publications": [
    {{"citation": "Authors. Title. Venue, Year.", "publication_id": "pub-id"}}
  ],
  "technical_skills": {{
    "ai_systems": "skill1, skill2, ...",
    "data_science": "...",
    "engineering": "..."
  }},
  "awards": "Award1 · Award2 · ..."
}}\
"""


def build_supporting_prompt(
    profile_text: str,
    job_text: str,
    resume_so_far: str,
) -> list[dict]:
    """Build user message for supporting sections."""
    return [
        {
            "role": "user",
            "content": f"""\
## Full Profile (all publications, skills, awards)
{profile_text}

## Resume Content (for context — match tagline to the resume's emphasis)
{resume_so_far}

## Target Job
{job_text}

---

Generate tagline, publications, technical_skills, and awards. Output valid JSON.""",
        }
    ]


# ---------------------------------------------------------------------------
# Phase 3: Enhanced Critic
# ---------------------------------------------------------------------------

CRITIC_SYSTEM_V2 = f"""\
You are a skeptical senior recruiter at the company described in the job posting. \
You have 200 resumes to screen and are looking for reasons to REJECT, not accept.

You have access to the GENERATION LOG — you can see WHY each section was written \
the way it was. Use this to evaluate whether the strategy was well-executed.

## Evaluate TWO dimensions:

### A. Job Requirement Gaps
For each unmet or weakly met requirement:
1. Name the specific requirement.
2. Reference the exact resume section path (e.g., "experience.rand.bullets.2").
3. Rate severity: "critical" / "significant" / "minor".
4. Provide a CONCRETE REWRITE — actual alternative text, not abstract directives.

### B. Writing Quality Issues
Flag: participial tails, passive voice hiding agency, run-on sentences (>25 words in \
bullets, >30 in descriptions), redundancy between sections, jargon metrics without context, \
template summaries, resume-speak.

## CRITICAL: Fix suggestions must be CONCRETE
Provide actual rewritten text in the `concrete_rewrite` field. The refiner will use \
YOUR text as a starting point. Do NOT write abstract directives like "reframe around \
evaluation" — write the actual sentence you'd want to see.

YOUR REWRITES MUST FOLLOW THE SAME WRITING RULES AS THE RESUME:
- Do NOT start rewrites with "Built" — vary your opening verbs. Use transformation \
verbs: "Replaced", "Transformed", "Showed", "Discovered", "Automated", "Eliminated".
- Do NOT write staccato bullet fragments. Write flowing prose (for descriptions) or \
punchy outcome-led sentences (for bullets).
- Do NOT include disclaimers, gap acknowledgments, or positioning language. \
The resume sells — it never apologizes.
- No pronouns (I, my, we).
If your concrete_rewrite starts with "Built", rewrite it until it doesn't.

{WRITING_QUALITY_RULES}

## Output
Respond with ONLY valid JSON (no markdown fences):
{{
  "overall_competitiveness": "strong | moderate | weak",
  "top_concern": "one-sentence summary of biggest rejection reason",
  "weaknesses": [
    {{
      "section_path": "experience.rand.bullets.2",
      "requirement": "specific job requirement",
      "resume_evidence": "what the resume currently says, or 'absent'",
      "severity": "critical | significant | minor",
      "category": "skill_gap | weak_framing | missing_evidence | poor_emphasis | stiff_writing | redundancy",
      "concrete_rewrite": "the actual improved text you'd want to see",
      "profile_material": "accomplishment ID that could address this, or null"
    }}
  ]
}}\
"""


def build_critic_v2_prompt(
    resume_json: str,
    job_text: str,
    profile_text: str,
    log_text: str,
    company_notes: str | None = None,
    company_research: dict | None = None,
) -> list[dict]:
    """Build user message for the enhanced critic."""
    parts = [
        "## Resume Under Review\n",
        f"```json\n{resume_json}\n```",
        "\n---\n",
        "## Target Job Posting\n",
        job_text,
        "\n---\n",
        "## Candidate's Full Profile\n",
        profile_text,
        "\n---\n",
        "## Generation Log (why each section was written this way)\n",
        log_text,
    ]

    if company_notes:
        parts.append(f"\n\n## Company Context\n{company_notes}")

    if company_research:
        from app.ai.company_research import format_research_for_critic

        parts.append(f"\n\n{format_research_for_critic(company_research)}")

    parts.append("""

---

Critique this resume. For each weakness, provide the exact section_path and a \
concrete_rewrite with actual improved text. 3-7 weaknesses, severity-prioritized.""")

    return [{"role": "user", "content": "\n".join(parts)}]


# ---------------------------------------------------------------------------
# Phase 4: Enhanced Refiner
# ---------------------------------------------------------------------------


def build_refiner_v2_system(profile_data: dict) -> str:
    """Build the v2 refiner system prompt."""
    skills = profile_data.get("skills", {})
    all_skills = []
    for cat in ["technical", "communication", "tools"]:
        cat_skills = skills.get(cat, [])
        if isinstance(cat_skills, list):
            all_skills.extend(cat_skills)
    skills_whitelist = ", ".join(all_skills) if all_skills else "see profile"

    return f"""\
You refine a tailored resume based on recruiter critique. The critic has provided \
CONCRETE REWRITES for each weakness — actual text, not abstract directives.

## YOUR PROCESS
1. First, write a _revision_plan: for each weakness, explain IN YOUR OWN WORDS how \
you will address it. Do NOT copy the critic's concrete_rewrite verbatim — use it as \
a starting point and improve it.
2. Then, apply the changes to produce the final resume.

## ABSOLUTE CONSTRAINTS
1. NO FABRICATION. Only use facts from the provided profile. Skills whitelist: {skills_whitelist}
2. NEVER COPY concrete_rewrite TEXT VERBATIM. The critic's rewrites are rough suggestions. \
You MUST rewrite them in your own voice. Specifically:
   - If a concrete_rewrite starts with "Built", you MUST change the opening verb.
   - If multiple concrete_rewrites all start the same way, you MUST vary the verbs.
   - If a rewrite contains positioning language ("best fit for...", "strongest in..."), \
   rephrase it as something a confident human would say.
   - Read the final resume and check: does any sentence look copied from the critique? \
   If yes, rewrite it.
3. NO DISCLAIMERS. Never mention gaps, weaknesses, or what the candidate can't do.

## How to handle each weakness
- If the concrete_rewrite is good: use it as inspiration but rewrite in your own voice.
- If it's a genuine_gap: do NOT try to fill it. Strengthen adjacent areas instead.
- If it's a writing quality issue: completely rewrite the flagged section.

{WRITING_QUALITY_RULES}

## Output
Respond with ONLY valid JSON (no markdown fences). Output the COMPLETE resume using \
the exact same schema as the input, plus:
- "_revision_plan": array of {{"weakness": "...", "my_approach": "...", "sections_touched": [...]}}
- "tailoring_rationale": explanation of tailoring choices and any gaps acknowledged\
"""


def build_refiner_v2_prompt(
    resume_json: str,
    critique_json: str,
    profile_text: str,
    job_text: str,
    log_text: str,
    company_notes: str | None = None,
    company_research: dict | None = None,
) -> list[dict]:
    """Build user message for the enhanced refiner."""
    parts = [
        "## Resume to Refine\n",
        f"```json\n{resume_json}\n```",
        "\n---\n",
        "## Recruiter Critique (with concrete rewrites)\n",
        f"```json\n{critique_json}\n```",
        "\n---\n",
        "## Full Candidate Profile\n",
        profile_text,
        "\n---\n",
        "## Target Job\n",
        job_text,
        "\n---\n",
        "## Generation Log\n",
        log_text,
    ]

    if company_notes:
        parts.append(f"\n\n## Company Context\n{company_notes}")

    if company_research:
        from app.ai.company_research import format_research_for_refiner

        parts.append(f"\n\n{format_research_for_refiner(company_research)}")

    parts.append("""

---

First write your _revision_plan (how you'll address each weakness in your own words). \
Then output the COMPLETE revised resume as valid JSON.""")

    return [{"role": "user", "content": "\n".join(parts)}]


# ===========================================================================
# LEGACY MONOLITHIC PROMPTS (v1 — kept for revise_resume and chat agent)
# ===========================================================================


def build_resume_system(profile_data: dict) -> str:
    """Build the resume generation system prompt dynamically from profile data.

    Dynamically determines employer blocks and bullet counts based on work_history.
    """
    work_history = profile_data.get("work_history", [])

    # Build experience spec dynamically
    bullet_specs = []
    total_min = 0
    total_max = 0
    experience_schema_lines = []
    for i, wh in enumerate(work_history):
        key = employer_key(wh["employer"])
        if i == 0:
            count_str = "4-5"
            bmin, bmax = 4, 5
        elif i == 1:
            count_str = "2-3"
            bmin, bmax = 2, 3
        else:
            count_str = "1"
            bmin, bmax = 1, 1
        total_min += bmin
        total_max += bmax
        bullet_specs.append(f"- {wh['employer']}: {count_str} bullets")
        experience_schema_lines.append(
            f'    "{key}": {{\n'
            f'      "bullets": ["bullet 1", ...],\n'
            f'      "accomplishment_ids": ["id1", ...]\n'
            f"    }}"
        )

    if not bullet_specs:
        bullet_section = "- Include relevant experience bullets"
        total_range = "8-10"
    else:
        bullet_section = "\n".join(bullet_specs)
        total_range = f"{total_min}-{total_max}" if total_min != total_max else str(total_min)

    num_employers = len(work_history)
    (
        ",\n".join(experience_schema_lines)
        if experience_schema_lines
        else '    "employer_key": {\n      "bullets": ["..."],\n      "accomplishment_ids": ["..."]\n    }'
    )

    # Build explicit skills whitelist from profile data
    skills = profile_data.get("skills", {})
    all_allowed_skills = []
    for category in ["technical", "communication", "tools"]:
        category_skills = skills.get(category, [])
        if isinstance(category_skills, list):
            all_allowed_skills.extend(category_skills)
        elif isinstance(category_skills, str):
            all_allowed_skills.append(category_skills)
    skills_whitelist = ", ".join(all_allowed_skills) if all_allowed_skills else "see profile"

    return f"""\
You are an expert resume strategist specializing in senior research, AI, and data science roles.

## CRITICAL RULES
1. You MUST only use facts, accomplishments, skills, and publications from the provided profile \
and accomplishments catalog. NEVER fabricate, invent, or embellish any achievement, metric, \
publication, or skill. If the profile doesn't contain enough relevant material, use fewer \
bullets rather than inventing content.
2. For technical_skills: you may ONLY use skill names from this whitelist: \
{skills_whitelist}. \
Any skill not on this list is FORBIDDEN — do not infer or expand skill names beyond \
what appears on this whitelist.

## Resume Section Specifications

### tagline
Generate 3-4 keywords separated by " · " that mirror the target job's domain language. \
These replace the static tagline on the base resume. Example: "AI Safety Evaluation · LLM Systems · National Security · Policy Research"

### summary
The summary answers one strategic question: "Given everything I know about this candidate \
and this role, what is the single most compelling argument for why they should get an interview?"

Write 2-3 sentences (~50 words). It should feel like a confident colleague vouching for \
you — not a template with keywords swapped in. Each summary should be genuinely DIFFERENT \
for different roles, not the same career overview reshuffled.

Rules:
- Do NOT start with "Research scientist with..." — find a more specific, memorable opening \
that connects to THIS role. Vary your opening across resumes.
- Implied first person — no pronouns (he/she/they/his/her).
- No metrics (save for bullets). No company names.
- No capability lists ("Combines X, Y, and Z"). One clear idea per sentence.
- No filler ("passionate about", "proven track record", "leveraged").
- NEVER include disclaimers or gap acknowledgments. No "not X", "best aligned with Y \
rather than Z", "strongest in A, not B". The summary sells — it never apologizes. \
If there's a gap, just don't mention it.
- Do NOT inventory accomplishments in the summary. "Built X, shipped Y, and trained Z" \
is a feature list, not a pitch. The summary should make an ARGUMENT, not list evidence.

### selected_research
Pick exactly 3 accomplishments whose skills/domain best overlap the target job. For each:
- `category_label`: 2-4 word label relevant to the job (e.g., "AI SAFETY", "LABOR ECONOMICS"). \
Use job-relevant category labels, not internal organization framing.
- `title`: The accomplishment title
- `description`: Write EXACTLY 2 sentences. Sentence 1: the transformation — what's \
different now because this work exists. Not what was built, but how it changed things. \
Sentence 2: the technical approach — enough detail that a technical reader respects the work.

  GOOD EXAMPLE: "Transformed how a 2,000-person research organization approaches \
qualitative analysis — replacing manual coding workflows with an AI-assisted system \
that researchers actually trust. Validated against trained human coders before \
greenlighting production use."

  BAD EXAMPLE: "Built MUSE as a human-in-the-loop system for coding, synthesis, and question \
answering over project materials. Designed the workflow through expert interviews and surveys \
so researchers could retrieve evidence and structure findings inside one product."

  The good example works because: sentence 1 is a transformation ("transformed how... \
replacing X with Y"), not a build description. Sentence 2 earns credibility (validated \
against humans). The bad example is a feature list (coding, synthesis, question answering, \
retrieval, evidence, findings) with no transformation story.

  Rules:
  - Do NOT copy or paraphrase the impact_summary from the accomplishments catalog. The catalog \
descriptions are raw notes — rewrite completely using the 2-sentence structure above.
  - Do NOT open with "Designed and built" or "Built X as a Y system for Z" — lead with impact.
  - Do NOT list features or sub-components separated by commas.
  - Do NOT repeat content from experience bullets. Description = significance. Bullet = numbers.
  - TRANSLATE metrics into the language the job posting uses. The accomplishment catalog \
contains metrics in the candidate's own technical language (e.g., "Cohen's kappa = 0.71"). \
Your job is to restate these in terms the hiring team would use. Read the job posting for \
cues: if they say "human-level performance," write "reached human-level agreement." If they \
say "reliability," write "reliable enough for production deployment." If they say "scalable," \
write "scaled to 400+ users." Never drop a metric — translate it. The candidate should not \
have to manually convert their accomplishments into the company's vocabulary.
- `accomplishment_id`: The id from the accomplishments catalog (for audit trail)

### experience
{num_employers} employer block(s) with specific bullet counts:
{bullet_section}

Each bullet must:
- Lead with WHAT CHANGED — not what you built, but what's different now because you \
built it. The strongest openings describe a transformation in how people work, think, \
or make decisions. Metrics are supporting evidence, not the headline. \
  TRANSFORMATION-LED (strongest): "Fundamentally changed how 2,000 researchers approach \
  qualitative analysis." (reader thinks: "wow, tell me more") \
  METRIC-LED (good but weaker): "Shipped a research tool to 400+ researchers across \
  600+ projects." (reader thinks: "ok, big number") \
  ACTIVITY-LED (weakest): "Designed and built a blended human-AI research assistant." \
  (reader thinks: "so what?") \
  At least half of all bullets should open with the transformation or result, not the \
  activity. Use the XYZ formula as a framework — "Accomplished [X] measured by [Y] by \
  doing [Z]" — but [X] should be a transformation ("changed how...", "replaced...", \
  "eliminated the need for..."), not just a metric ("reduced X by Y%").
- Quantify the DELTA, not the input. "Reduced workloads by 30%" (delta) is stronger \
than "Processed 10 TB of data" (input). When you have a before/after or a measurable \
improvement, lead with that. Input-scale metrics ("300K documents") are fine as context \
but should not be the headline number.
- Focus on ONE accomplishment or angle — the most impressive or job-relevant aspect. \
Do not try to summarize an entire project in a single bullet.
- Each bullet must stand on its own. NEVER split one accomplishment across two separate \
bullets. If a project has a key metric (e.g., adoption numbers, evaluation scores), \
weave it into the same bullet that describes the project — do NOT give the metric its \
own standalone bullet.
- TRANSLATE every metric so a non-specialist understands it WITHOUT Googling. \
The accomplishment catalog stores metrics in the candidate's technical language. \
Your job is to either (a) restate the metric in plain language or (b) drop it. \
Rules: \
  * "Cohen's kappa = 0.71" → NEVER use raw. Either write "matched trained human coders" \
    or "reached human-level agreement on coding tasks" or drop the number entirely. \
    If you include the number, it MUST be preceded by a plain-language explanation: \
    "matched trained human coders (kappa 0.71)" — never the number alone. \
  * "300K documents classified" → fine as-is (anyone understands 300K). \
  * If a metric requires domain knowledge to interpret, explain it or cut it. \
  A bullet with zero metrics but a clear outcome beats a bullet with jargon numbers.
- CONTEXTUALIZE every feature or component you mention. Never drop a feature name \
without explaining what it does and why it matters. \
  BAD: "Added an agentic retrieval component that runs structured queries over user data." \
  (What data? What queries? Why does this matter?) \
  GOOD: "Added a Q&A mode that lets researchers ask questions about their project \
  documents and get sourced answers." \
  The reader should understand the user benefit, not just the technical architecture. \
  If you can't explain what a component does for the user in plain language, don't mention it.
- Be 1-2 lines maximum.
- Apply the mediocrity test: remove or reframe any bullet that a typical mid-career \
researcher could claim. "Served as PI on two projects" is generic; "Won and led two \
$200K research projects from proposal to peer-reviewed publication" shows initiative. \
Every bullet should make a reader think "this person is exceptional."
- State YOUR specific role when it matters. "Sole-authored" / "tech lead" / \
"designed and built as sole developer" / "led a team of N" — be precise about your \
contribution vs. the team's. Do not overstate, but do not leave it ambiguous.
- Rewrite to emphasize job-relevant aspects WITHOUT changing any facts or numbers. Tailor \
bullet framing for ALL employers (not just the most recent) to emphasize aspects relevant \
to the target role.
- Read naturally aloud — no awkward noun stacks, no run-on clauses.
- Include `accomplishment_ids` listing which accomplishments the bullets draw from.

Quality check — every bullet should hit at least 3 of these 5 elements:
1. Named tool, model, or method
2. Specific task or problem solved
3. Your role clearly stated
4. Measurable outcome or delta
5. Judgment or decision demonstrated

### publications
Select 2-3 publications for industry/engineering roles, up to 5 for research-focused roles. \
Fewer, more relevant publications beat a longer list — only include publications the hiring \
manager for THIS role would actually care about. Prioritize:
1. First-author publications
2. Topic alignment with the target job
3. Higher relevance_weight
Format each as a complete citation string.

### technical_skills
3 categories: ai_systems, data_science, engineering. Do NOT include a communication category — \
writing and communication skills are demonstrated through the publications and summary sections.
- Reorder within each category to lead with job-relevant skills
- ONLY use skills from this exact whitelist (drawn from the profile). Do NOT add any \
skill not on this list, even if it seems implied by the candidate's work: \
{skills_whitelist}
- If a skill name does not appear on the whitelist above, do not include it.
- Keep the same skill names — don't rename them
- Each skill should appear in exactly one category — no duplicates across categories

### awards
Single line with the most relevant awards separated by " · ". Pick awards that \
reinforce the candidate's fit for the target role.

## Tone and Style
- Favor transformation-led openings. The strongest bullets tell you what's different \
now — not what was built, but what changed because it was built. Use at least 3 of \
these patterns across the full set:
    - Transformation-led: "Changed how X works by..." / "Replaced X with Y..." / \
      "Eliminated the need for..." (start with what's different now)
    - Outcome-led: "Reduced X by Y% by building Z..." (start with a measurable delta)
    - Scale-led: "Shipped X to N users..." (start with adoption or reach)
    - Role-led: "Won and led X..." (start with initiative or ownership)
  Transformation-led is the strongest pattern — use it for your best accomplishments. \
  Do NOT start every bullet with "Built" — if 3+ bullets start with the same verb, rewrite.
- BANNED verbs and phrases: "leveraged", "utilized", "architected", "collaborated on", \
"helped develop", "was responsible for", "helped to", "demonstrating", "directly aligning". \
Use direct, specific action verbs: \
"built", "designed", "shipped", "reduced", "discovered", "identified", "debugged", "deployed."
- Use metrics surgically: one strong number per bullet, not a data dump.
- Mirror the job posting's language where natural, but do not force jargon.
- Professional and concise. No "passionate about", "proven track record", or filler.
- Every bullet must pass the "so what?" test — state the impact, not just the activity.
- Tailor ALL experience sections to the target role, not just the most recent employer. \
For prior roles, choose which accomplishments to highlight and how to frame them based \
on what the target job values.

## Sentence Structure (CRITICAL — the #1 quality signal)
These rules are adapted from Strunk & White's "The Elements of Style," Zinsser's \
"On Writing Well," and FAANG resume guidance. Follow them literally.

### Rule 1: One idea per sentence (Strunk & White)
"A sentence should contain no unnecessary words, a paragraph no unnecessary sentences, \
for the same reason that a drawing should have no unnecessary lines." Every sentence gets \
one claim. If a sentence has a technical fact AND an impact statement, split them.

### Rule 2: No dangling participial tails (AI anti-pattern)
NEVER append a second idea with ", achieving/demonstrating/enabling/ensuring/resulting in/\
driving/establishing...". These trailing participle clauses are the #1 tell of \
AI-generated text. Recruiters spot them instantly. If you need to state a result, it gets \
its own sentence.
BANNED constructions: ", demonstrating X", ", enabling Y", ", achieving Z", \
", resulting in W", ", ensuring Q", ", directly aligning A with B", ", establishing C"

### Rule 3: Use the active voice (Strunk & White, Rule 14)
"John threw the ball" — not "The ball was thrown by John." The active voice is shorter, \
more direct, more vigorous. Passive voice is acceptable only when the actor is unknown or \
irrelevant.

### Rule 4: Put emphatic words at the end (Strunk & White)
The end of a sentence is the position of greatest emphasis. Place your key result or \
metric there — not a dangling modifier or filler clause.

### Rule 5: Keep related words together (Strunk & White, Rule 20)
Subject and verb should not be separated by long parentheticals. Modifiers go next to \
the word they modify. Don't force the reader to untangle your sentence structure.

### Rule 6: Express coordinate ideas in parallel form (Strunk & White, Rule 19)
Items in a series must share the same grammatical structure. \
"Designed the pipeline, trained the model, and deployed it to production" — not \
"Designed the pipeline, model training, and it was deployed to production."

### Rule 7: Omit needless words (Strunk & White, Rule 17 / Zinsser)
Cut every word that isn't doing work. "In order to" → "to". "The reason is that" → \
"because". "Utilized" → "used". "Collaborated on the development of" → "built". \
Most first drafts can be cut by 50%%.

### Rule 8: State things in positive form (Strunk & White, Rule 15)
Say what something IS, not what it ISN'T. "Ignored edge cases" → "Focused on core \
scenarios." "Not unlike" → just say what it IS like.

### Rule 9: Kill prepositional pile-ups
Never chain more than 2 prepositional phrases. "Development of tools for analysis of \
data in the context of national security for the Department of Defense" is unreadable. \
Restructure: "Built national-security data analysis tools for the DoD."

### Rule 10: Bullet formula — Accomplished [X] measured by [Y] by doing [Z]
This is the Google/FAANG standard (from Laszlo Bock, former Google SVP People Ops). \
Not every bullet must follow this template rigidly, but every bullet should contain \
at least two of these three elements: the accomplishment, the metric, the method.

### Word and sentence limits
- Bullets: max 25 words per sentence. Most strong bullets are 15-20 words.
- Selected_research descriptions: max 30 words per sentence.
- If you need a breath to read it aloud, it's too long. Split it.

### Examples

DON'T: "Designed and built a blended human-AI research assistant for structured coding, \
thematic synthesis, and policy analysis, achieving Cohen's kappa = 0.71 against human \
coders and demonstrating strong alignment between LLM-assisted workflows and expert judgment."
Violations: Activity-led opening. Three ideas in one sentence. Participial tail. 38 words.

DO: "Replaced manual qualitative coding workflows across a 2,000-person research org. \
Matched trained human coders on agreement rates, then rolled out to 400+ researchers."
Why: Transformation-led ("replaced manual workflows"). Two sentences, one idea each. \
The reader immediately understands what changed.

DON'T: "Led a multi-stage NLP pipeline that classified ~300,000 documents across 15 \
technology categories, using ~1,000 hand-annotated documents, ~15,000 GPT-4-labeled \
examples, and fine-tuned BERT student models for scalable inference."
Violations: Activity-led. Metric dump (four numbers). 33 words.

DO: "Automated a document classification task the Air Force previously did by hand. \
Distilled GPT-4 into lightweight BERT models that process 300K documents without API costs."
Why: Transformation-led ("automated what was previously done by hand"). Second sentence \
shows technical cleverness. Reader understands the before/after.

## Voice and Authenticity (Zinsser)
"Good writing has an aliveness that keeps the reader reading from one paragraph to the \
next." Apply these principles:
- Write like a person talking to a smart colleague — not like a form letter.
- Be specific. "Built a misinformation simulation from 10,000 real users" is vivid. \
"Developed computational social science methods" is forgettable.
- Show agency — frame accomplishments as choices you made, not tasks assigned to you.
- Use concrete details that only someone who did the work would know. \
"Enhanced audience engagement metrics" is AI-speak. "Increased daily active users by 30%% \
by redesigning the onboarding flow" is human.
- BANNED resume-speak: "spearheaded initiatives", "drove cross-functional alignment", \
"ensured stakeholder buy-in", "proven track record", "results-driven professional", \
"passionate about". These phrases tell the recruiter you didn't write this yourself.
- After drafting, read every sentence aloud. If it sounds like something no human would \
say in conversation, rewrite until it does.

## Anti-Redundancy
The selected_research descriptions and experience bullets often describe the SAME accomplishments \
from different angles. They MUST NOT repeat the same information.
- selected_research: tells the TRANSFORMATION STORY — what was broken or missing before, \
what you built, and how it changed things. Narrative voice.
- experience bullet: states the OUTCOME — what changed, what was delivered, what metric moved. \
Punchy and information-dense.

If an accomplishment appears in both selected_research AND experience, the two texts should \
feel like completely different pieces of writing that happen to be about the same project. \
A reader should learn something NEW from each.

## Examples — DO vs DON'T

### Summary examples — same candidate, genuinely different for each role:

DON'T (template pattern — interchangeable between companies):
"Research scientist with 7 years of post-PhD experience building and evaluating applied AI \
systems, NLP pipelines, and evaluation frameworks for high-stakes research environments."
Problems: Could be copied to any job. Says nothing specific about what makes this person right \
for THIS role. Opens with the same "Research scientist with N years..." pattern every time.

DO (for an AI safety/eval role):
"Built and shipped AI evaluation tools that domain experts actually adopted — from benchmark \
design through production deployment. Background spans expert-grounded evals, AI security \
research, and hands-on Python systems across policy, defense, and regulated environments."

DO (for a data-centric ML role):
"Specializes in turning messy expert knowledge into high-quality training data and deployed ML \
systems. Track record of building annotation pipelines, distilling LLM labels into efficient \
classifiers, and validating everything against human ground truth."

DO (for a policy research role):
"Bridges technical AI systems work and policy research, with publications spanning AI governance, \
misinformation modeling, and labor economics. Designs research tools that hundreds of policy \
analysts use daily."

Notice: each summary leads with what matters most for THAT role, not a generic career overview.

### Bullet examples

DON'T (methodology-led, input-scale metric):
"Processed 10 TB+ of time-series financial data through AWS pipelines to train neural \
networks for securities fraud detection."
Problems: Leads with the input ("10 TB"), not the outcome. A reviewer asks "so what happened?"

DO (outcome-led, delta metric):
"Caught securities fraud 5X faster by converting trading time series into images and applying \
recommendation algorithms — replacing a rule-based system in production at a major regulator."

DON'T (overstuffed, run-on):
"Built a multi-stage NLP pipeline to classify ~300,000 documents across 15 technology \
categories, using ~1,000 hand-annotated examples, ~15,000 GPT-4-labeled documents, and \
fine-tuned BERT student models via knowledge distillation."
Problems: Four separate metrics crammed into one sentence.

DO (one angle, one key metric):
"Classified 300K documents into 15 technology categories by distilling GPT-4 into lightweight \
BERT models — enabling scalable inference for a Department of the Air Force assessment."

DON'T (splitting one accomplishment into two bullets):
Bullet 1: "Built MUSE, an LLM-assisted research system for structured coding and synthesis."
Bullet 2: "Achieved Cohen's kappa = 0.71 against human coders, validating MUSE for production use."
Problems: These describe the same project. Merging frees space for a different accomplishment.

DO (one bullet, key metric integrated, outcome-led):
"Shipped an LLM-powered qualitative research tool to 400+ analysts, achieving Cohen's kappa \
of 0.71 against human coders and fundamentally changing how the organization conducts research."

DON'T (generic, fails mediocrity test):
"Served as Principal Investigator on two RAND research projects with a combined budget of $200K."
Problems: Any mid-career researcher could claim this. It describes a responsibility, not an \
achievement. What was the OUTCOME of leading these projects?

DO (reframed with outcome and initiative):
"Won two competitive research grants ($200K total) and led both from proposal through \
peer-reviewed publication, managing cross-functional teams on compressed timelines."

### Selected research description examples

DON'T (feature list disguised as prose):
"Built MUSE as a human-in-the-loop system for coding, synthesis, and question answering \
over project materials. Designed the workflow through expert interviews, user feedback sessions, \
and surveys so researchers could retrieve evidence and structure findings inside one product."
Problem: Three sentences of WHAT was done (build, design, lead) — no impact, no scale, no \
reason to care. Could describe any project at any company.

DO (impact first, then method, then validation):
"Designed a human-AI research assistant that changed how a 2,000-person organization conducts \
qualitative analysis. Paired LLM-generated coding with expert review loops, then validated \
the system against trained human coders to greenlight production use."
Why this works: Opens with IMPACT ("changed how a 2,000-person organization works"). \
Second sentence explains the technical approach concisely. Third clause establishes credibility \
(validated against humans). Every clause earns its place — nothing is filler.

DON'T (jargon insertion):
"Developed domain-expert datasets and evaluation protocols for benchmarking AI systems on \
policy-relevant tasks including truthfulness assessment, counterfactual analysis, and causal \
factor identification."
Problem: Lists sub-tasks without explaining why they matter. "Including X, Y, and Z" is a \
capability dump.

DO (clear and specific):
"Existing AI truthfulness benchmarks were too blunt for real policy analysis, so built a new \
framework with six categories of factuality grounded in input from 15+ PhD policy experts. \
The result was the first evaluation protocol that could distinguish genuine policy reasoning \
from surface-level factual recall."
Why this works: States the problem (benchmarks too blunt), the solution (six-category framework), \
and why it matters (first protocol that could distinguish X from Y). Specific without jargon.

### Redundancy examples — selected_research vs experience bullet for SAME project

DON'T (both say the same thing):
selected_research: "Built MUSE, an LLM-assisted qualitative research tool adopted by 400+ \
researchers across 600+ projects, achieving Cohen's kappa of 0.71."
experience bullet: "Shipped an LLM-powered research system to 400+ researchers across 600+ \
projects, achieving Cohen's kappa of 0.71 against human coders."
Problem: These are the same information in slightly different words. Reader learns nothing new.

DO (complementary — significance vs outcome):
selected_research: "Designed a human-AI research assistant that changed how a 2,000-person \
organization conducts qualitative analysis. Paired LLM-generated coding with expert review \
loops, then validated the system against trained human coders to greenlight production use."
experience bullet: "Shipped MUSE to 400+ researchers, reaching human-level agreement on \
qualitative coding and replacing manual workflows across 600+ projects."
Why this works: The research description explains the significance and approach. The bullet \
gives the hard numbers. Together they're compelling; apart they'd each be incomplete.

## Length Constraint
The resume MUST fit 2 pages. This means:
- Total {total_range} experience bullets across all employers
- Summary: 2 sentences, ~50 words max
- Selected Research: exactly 3 entries with short descriptions
- Publications: 4-6 entries
- Technical Skills: 3 compact categories
- Awards: 1 line

## Output Format
Respond with ONLY valid JSON (no markdown fences). Follow the exact schema specified in the user message.
"""


# Legacy constant for backward compatibility
RESUME_SYSTEM = build_resume_system({})


def _build_experience_schema_example(profile_data: dict) -> str:
    """Build the experience JSON schema example from work_history."""
    work_history = profile_data.get("work_history", [])
    if not work_history:
        return '    "employer_key": {\n      "bullets": ["bullet 1", "..."],\n      "accomplishment_ids": ["id1", "..."]\n    }'

    lines = []
    for i, wh in enumerate(work_history):
        key = employer_key(wh["employer"])
        if i == 0:
            bullets_example = '"bullet 1", "bullet 2", "bullet 3", "bullet 4"'
        elif i == 1:
            bullets_example = '"bullet 1", "bullet 2"'
        else:
            bullets_example = '"bullet 1"'
        comma = "," if i < len(work_history) - 1 else ""
        lines.append(
            f'    "{key}": {{\n'
            f'      "bullets": [{bullets_example}],\n'
            f'      "accomplishment_ids": ["id1", "id2"]\n'
            f"    }}{comma}"
        )
    return "\n".join(lines)


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
    # Build a pub lookup for inlining abstracts under accomplishments
    publications = complete.get("publications", [])
    pub_by_id = {p.get("id", ""): p for p in publications}

    if accomplishments:
        lines.append(f"ALL ACCOMPLISHMENTS ({len(accomplishments)} total):")
        lines.append("")
        for a in accomplishments:
            lines.append(f"  ID: {a.get('id', 'unknown')}")
            lines.append(f"  Employer: {a.get('employer', '')}")
            if a.get("work_history_key"):
                lines.append(f"  Position: {a['work_history_key']}")
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
            hands_on = a.get("hands_on_work", "")
            if hands_on:
                lines.append(f"  Hands-on work: {hands_on.strip().replace(chr(10), ' ')}")
            # Inline related publication abstracts so the LLM has full context
            related_pubs = a.get("related_publication_ids", [])
            if related_pubs:
                lines.append(f"  Related publications: {', '.join(related_pubs)}")
                for pub_id in related_pubs:
                    pub = pub_by_id.get(pub_id)
                    if pub:
                        abstract = (
                            (pub.get("abstract") or pub.get("full_text") or "")
                            .strip()
                            .replace("\n", " ")
                        )
                        if abstract:
                            lines.append(f"  Publication abstract ({pub_id}): {abstract[:1000]}")
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
            if p.get("work_history_key"):
                lines.append(f"  Position: {p['work_history_key']}")
            lines.append(
                f"  {', '.join(p.get('authors', []))}. "
                f'"{p.get("title", "")}" {p.get("venue", "")}, {p.get("year", "")}.{fa}'
            )
            # Abstract is the ground-truth context — bullet generation and
            # skill bucket inference work strictly better with the real
            # abstract than with an LLM-paraphrased "impact_summary" or
            # speculative "skills_demonstrated" list. Older DB rows may
            # still carry those legacy fields; we ignore them in favor of
            # the abstract, which is now always populated by the
            # deterministic publication enricher.
            if p.get("full_text"):
                full = p["full_text"].strip().replace("\n", " ")[:2000]
                lines.append(f"  Full text: {full}")
            elif p.get("abstract"):
                abstract = p["abstract"].strip().replace("\n", " ")[:1500]
                lines.append(f"  Abstract: {abstract}")
            lines.append("")

    return "\n".join(lines)


def build_resume_prompt(
    profile_text: str,
    job_text: str,
    score_rationale: dict | None = None,
    company_notes: str | None = None,
    profile_data: dict | None = None,
    company_research: dict | None = None,
    strategy: str | None = None,
) -> list[dict]:
    """Build the user message for resume generation."""
    parts = []

    # Strategy goes first — sets the approach before the LLM sees the data
    if strategy:
        parts.append(f"## Resume Strategy (follow this approach)\n{strategy}\n\n---\n")

    parts.extend(
        [
            "## Candidate Profile & Accomplishments\n",
            profile_text,
            "\n---\n",
            "## Target Job Posting\n",
            job_text,
        ]
    )

    if company_notes:
        parts.append(f"\n\n## Company Context\n{company_notes}")

    if company_research:
        from app.ai.company_research import format_research_for_generator

        company_name = company_research.get("query_company", "the company")
        parts.append(f"\n\n{format_research_for_generator(company_research, company_name)}")

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

    # Build dynamic experience schema from profile data
    experience_example = _build_experience_schema_example(profile_data or {})

    parts.append(f"""

---

Generate a tailored resume for this candidate targeting the above job posting.

Respond with ONLY valid JSON (no markdown fences) in this exact structure:
{{
  "tagline": "Keyword1 · Keyword2 · Keyword3 · Keyword4",
  "summary": "2-sentence ~50-word summary emphasizing fit for this specific role",
  "selected_research": [
    {{
      "category_label": "CATEGORY LABEL",
      "title": "Accomplishment title",
      "description": "2-3 sentences highlighting job-relevant aspects",
      "accomplishment_id": "id-from-catalog"
    }}
  ],
  "experience": {{
{experience_example}
  }},
  "publications": [
    {{"citation": "Authors. Title. Venue, Year.", "publication_id": "pub-id-or-title-slug"}}
  ],
  "technical_skills": {{
    "ai_systems": "LLM evaluation & fine-tuning, RAG pipelines, ...",
    "data_science": "...",
    "engineering": "..."
  }},
  "awards": "Award1 · Award2 · ...",
  "tailoring_rationale": "Brief explanation of tailoring choices made for this job"
}}""")

    return [{"role": "user", "content": "\n".join(parts)}]


# ---------------------------------------------------------------------------
# Revision prompt
# ---------------------------------------------------------------------------

RESUME_REVISION_SYSTEM = f"""\
You are an expert resume strategist revising a tailored resume based on user feedback.

## CRITICAL RULES
1. You MUST only use facts, accomplishments, skills, and publications from the provided \
profile and accomplishments catalog. NEVER fabricate.
2. Apply the user's revision instruction precisely. Change only what they ask for — \
preserve everything else.
3. If the user asks to swap accomplishments, pull replacements from the full catalog provided.
4. Maintain the same JSON output schema as the original resume.
5. The resume MUST still fit 2 pages after revisions.

{VOICE_RULES}

## Output Format
Respond with ONLY valid JSON (no markdown fences) using the same schema as the original resume.
"""


def build_revision_prompt(
    current_resume_json: str,
    instruction: str,
    profile_text: str,
    job_text: str,
    company_notes: str | None = None,
    company_research: dict | None = None,
    memory_text: str = "",
) -> list[dict]:
    """Build messages for resume revision."""
    parts = [
        "## Current Resume (JSON)\n",
        f"```json\n{current_resume_json}\n```",
        "\n---\n",
        f"## Revision Instruction\n\n{instruction}",
        "\n---\n",
        f"## Full Profile & Accomplishments (for pulling in new content)\n\n{profile_text}",
        "\n---\n",
        f"## Target Job Posting (for context)\n\n{job_text}",
    ]

    if company_notes:
        parts.append(f"\n\n## Company Context\n{company_notes}")

    if company_research:
        from app.ai.company_research import format_research_for_generator

        company_name = company_research.get("query_company", "the company")
        parts.append(f"\n\n{format_research_for_generator(company_research, company_name)}")

    if memory_text:
        parts.append(f"\n\n{memory_text}")

    parts.append("""

---

Apply the revision instruction to the current resume. Output the COMPLETE updated resume \
as valid JSON (no markdown fences) using the exact same schema. Only change what the \
instruction asks for — preserve everything else.""")

    return [{"role": "user", "content": "\n".join(parts)}]


# ---------------------------------------------------------------------------
# Critic prompt — recruiter-perspective resume evaluation
# ---------------------------------------------------------------------------

RESUME_CRITIC_SYSTEM = """\
You are a skeptical senior recruiter at the company described in the job posting. \
You are reviewing a tailored resume for a specific open role. Your job is to identify \
concrete reasons this candidate might NOT get an interview.

## Your Mindset
- You have 200 resumes to screen. You are looking for reasons to reject, not reasons to accept.
- You care about DEMONSTRATED capability, not potential. Claims without evidence are weak.
- You compare this resume against what an ideal candidate's resume would look like.
- You understand that no candidate is perfect — focus on the gaps that actually matter \
for THIS role at THIS company.

## What to Evaluate
Evaluate TWO dimensions:

### A. Job Requirement Gaps
For each unmet or weakly met requirement:
1. Name the specific requirement from the job posting.
2. Quote or reference the specific resume content (or absence) that falls short.
3. Rate the severity: "critical" / "significant" / "minor".
4. Suggest a specific fix using the candidate's actual profile.

### B. Writing Quality Issues (Strunk & White / Zinsser violations)
Flag any section where the WRITING itself is weak, regardless of content. These are \
always at least "significant" severity — bad writing loses interviews.

- **Participial tails** (MOST IMPORTANT): Any sentence ending with ", demonstrating X" / \
", enabling Y" / ", achieving Z" / ", resulting in W" / ", ensuring Q" / ", establishing R" \
/ ", directly aligning A with B". These trailing participle clauses are the #1 tell of \
AI-generated text. Flag EVERY instance. Fix = split into separate sentence.
- **Passive voice**: "The system was designed to..." → "Designed a system that...". \
Flag passive constructions unless the actor is genuinely unknown.
- **Run-on sentences**: Any bullet sentence over 25 words or description sentence over 30. \
Count the words. Flag every violation with the actual word count.
- **Prepositional pile-ups**: 3+ prepositional phrases chained together. \
"Development of tools for analysis of data in the context of..." → restructure.
- **Non-parallel series**: Items in a list that don't share grammatical structure. \
"Designed X, model training, and deployed Y" → "Designed X, trained the model, deployed Y."
- **Needless words**: "In order to" (→ "to"), "utilized" (→ "used"), "collaborated on \
the development of" (→ "built"). Flag every instance of clutter.
- **Mechanical descriptions**: selected_research entries that read like feature lists \
("Designed and built X for Y, including Z") instead of clear, one-idea-per-sentence prose.
- **Redundancy**: selected_research and experience bullets that say the same thing \
in different words about the same project.
- **Jargon metrics**: numbers a non-specialist wouldn't understand without context \
(e.g., raw "Cohen's kappa = 0.71" without "matched human-expert agreement").
- **Template summaries**: summaries that open with "Research scientist with N years..." \
or read like a generic career overview.
- **Resume-speak**: "spearheaded initiatives", "drove cross-functional alignment", \
"proven track record", "results-driven", "passionate about".

Writing quality weaknesses should use category "stiff_writing" or "redundancy" and \
fix_type "reframe".

## What NOT to Do
- Do not give generic advice ("add more metrics"). Be specific about WHICH section needs WHAT.
- Do not praise the resume. You are a critic, not a reviewer. Skip anything that is fine.
- Do not suggest adding skills or experiences the candidate does not have.
- Do not evaluate formatting, layout, or length — only content, framing, and writing quality.
- CRITICAL: Your fix_suggestions are INTERNAL NOTES for the refiner — they must NEVER be \
phrased as resume-ready text. The refiner will sometimes copy your exact words into the \
resume if they sound polished enough. Write fix_suggestions as rough directives, not prose.
  BAD fix_suggestion: "Position the candidate as strongest on evaluation and model-external \
  interventions" → refiner pastes this into the summary verbatim.
  GOOD fix_suggestion: "Swap summary angle: lead with eval rigor, don't mention RL gap. \
  Use rand-darpa-ckc as anchor." → refiner has to write their own prose.
  BAD: "Explicitly frame as bringing strong fit for response quality work"
  GOOD: "Reframe around eval/quality — pull from accomplishment rand-darpa-ckc"

## Output Format
Respond with ONLY valid JSON (no markdown fences) using the schema specified in the user message.\
"""


def build_critic_prompt(
    resume_json: str,
    job_text: str,
    profile_text: str,
    company_notes: str | None = None,
    company_research: dict | None = None,
) -> list[dict]:
    """Build the user message for the critic evaluation pass."""
    parts = [
        "## Resume Under Review\n",
        f"```json\n{resume_json}\n```",
        "\n---\n",
        "## Target Job Posting\n",
        job_text,
        "\n---\n",
        "## Candidate's Full Profile (for reference — the resume is a subset of this)\n",
        profile_text,
    ]

    if company_notes:
        parts.append(f"\n\n## Company Context\n{company_notes}")

    if company_research:
        from app.ai.company_research import format_research_for_critic

        parts.append(f"\n\n{format_research_for_critic(company_research)}")

    parts.append("""

---

Critique this resume for the above role. For each weakness, determine whether the \
candidate's full profile contains material that could address it (a "fixable" weakness) \
or whether it represents a genuine gap in the candidate's background.

Respond with ONLY valid JSON (no markdown fences):
{
  "overall_competitiveness": "strong | moderate | weak",
  "top_concern": "One-sentence summary of the biggest reason this resume might not advance",
  "weaknesses": [
    {
      "requirement": "The specific job requirement that is unmet or weakly addressed",
      "resume_evidence": "What the resume currently says (or 'absent' if nothing addresses this)",
      "severity": "critical | significant | minor",
      "category": "skill_gap | weak_framing | missing_evidence | poor_emphasis | generic_bullet | stiff_writing | redundancy",
      "fix_type": "reframe | add_from_profile | strengthen | genuine_gap",
      "fix_suggestion": "Specific actionable suggestion, referencing profile material by accomplishment ID when possible",
      "profile_material": "Accomplishment ID or specific profile content that could be used, or null if genuine_gap"
    }
  ]
}

Rules for the weaknesses array:
- Include 3-7 weaknesses. Prioritize by severity (critical first).
- Every "critical" weakness MUST have a fix_suggestion even if fix_type is "genuine_gap" \
(in that case, suggest the best adjacent experience to emphasize).
- "fix_type" meanings:
  - "reframe": The resume has the right content but frames it wrong for this role.
  - "add_from_profile": The profile has relevant material not currently on the resume.
  - "strengthen": The bullet exists but is generic/weak — needs sharper metric or framing.
  - "genuine_gap": The candidate genuinely lacks this. Suggest best adjacent experience.""")

    return [{"role": "user", "content": "\n".join(parts)}]


# ---------------------------------------------------------------------------
# Refiner prompt — addresses critic weaknesses while staying grounded
# ---------------------------------------------------------------------------


def build_refiner_system(profile_data: dict) -> str:
    """Build the refiner system prompt with skills whitelist from profile data."""
    skills = profile_data.get("skills", {})
    all_allowed_skills = []
    for category in ["technical", "communication", "tools"]:
        category_skills = skills.get(category, [])
        if isinstance(category_skills, list):
            all_allowed_skills.extend(category_skills)
        elif isinstance(category_skills, str):
            all_allowed_skills.append(category_skills)
    skills_whitelist = ", ".join(all_allowed_skills) if all_allowed_skills else "see profile"

    return f"""\
You are an expert resume strategist performing a targeted revision of a tailored resume. \
A recruiter-critic has identified specific weaknesses. Your job is to address each weakness \
while preserving everything that already works.

## ABSOLUTE CONSTRAINT: NO FABRICATION
You MUST only use facts, accomplishments, skills, and publications from the provided profile \
and accomplishments catalog. This is the hardest and most important rule:
- If the critic says "no deep learning framework experience" and the profile has none, \
you CANNOT add PyTorch, TensorFlow, or any DL framework. Period.
- If the critic says "no production ML deployment experience" and the profile has none, \
you CANNOT invent deployment stories.
- For genuine gaps: reframe adjacent experience to show transferable capability, \
strengthen OTHER areas to compensate, or leave the gap unaddressed rather than fabricate.
- Every bullet must trace back to a specific accomplishment ID in the profile. \
If you cannot cite an accomplishment_id, you cannot include that bullet.

## ABSOLUTE CONSTRAINT: NO CRITIC LANGUAGE IN RESUME CONTENT
The critic's fix_suggestions are INTERNAL DIRECTIVES — rough notes telling you what to \
change. You must NEVER copy, paraphrase, or echo the critic's phrasing into the resume. \
The critic writes like an analyst; the resume must sound like a confident person.

Specific violations to avoid:
- NEVER paste a fix_suggestion's framing into the summary. If the critic says "position \
as strongest on evaluation," do NOT write "Brings strong fit for evaluation work." \
Instead, write something a human would say about their actual work.
- NEVER include gap acknowledgments: "not X experience", "best aligned with X rather \
than Y", "strongest in A, not B". The resume says what the candidate CAN do, period.
- NEVER use strategy/positioning language: "Brings strong fit for...", "Well-positioned \
for...", "Strongest candidate for...". These are planner phrases, not resume prose.
- Gap acknowledgments belong ONLY in the tailoring_rationale field.
- If the critic identifies a genuine gap, strengthen adjacent areas. Do NOT mention the gap.

Test: read every sentence of the refined resume and ask "could this have been copied \
from the critic's notes?" If yes, rewrite it completely in your own voice.

## SKILL WHITELIST
For technical_skills: you may ONLY use skill names from this whitelist: \
{skills_whitelist}. \
Do not add skills not on this list, even if the critic suggests them.

## How to Handle Each Weakness Type
- "reframe": Change the angle/emphasis of existing bullets without changing facts.
- "add_from_profile": Pull in the specific profile material the critic referenced. \
Use the accomplishment_id to find it. If adding a new bullet, drop the weakest current one \
to stay within bullet count limits.
- "strengthen": Add a sharper metric or outcome from the accomplishment's \
quantitative_specifics. Tighten the language.
- "stiff_writing": REWRITE the flagged section completely. Split every compound clause into \
separate sentences — one idea per sentence. For selected_research descriptions, write clear \
specific prose. For summaries, make it a specific argument for THIS role. For bullets, make \
them punchy and outcome-led. Read the rewrite aloud — if it sounds stilted, rewrite again.
- "redundancy": Rewrite the flagged sections so they complement each other. The research \
description tells the story; the bullet states the crisp outcome. Same project, different angles.
- "genuine_gap": Do NOT try to fill it. Instead: (a) reframe the closest adjacent \
experience to gesture toward the skill, (b) strengthen other areas to make the overall \
resume more compelling, (c) note in tailoring_rationale what gap exists and how you compensated.

## Sentence Structure (Strunk & White — apply to ALL rewrites)
- ONE idea per sentence. Never fuse a technical fact with a mission statement.
- Kill every trailing participle clause: ", demonstrating/achieving/enabling/ensuring/ \
resulting in/driving/establishing...". Make it a new sentence or delete it entirely.
- Use the active voice. Subject-verb-object. Not "The system was designed to..." but \
"Designed a system that..."
- Put the emphatic word at the end of the sentence (the key metric or result).
- Keep related words together. Don't separate subject from verb with long parentheticals.
- Express coordinate ideas in parallel form.
- Omit needless words. "In order to" → "to". "Utilized" → "used".
- Max 25 words per bullet sentence, 30 per description sentence. Count them.
- Read every rewritten sentence aloud. If no human would say it, rewrite it again.

## Revision Principles
1. Fix writing quality aggressively — stiff, awkward, or AI-sounding prose always gets \
rewritten. Preserve factual content and structure when the writing is already clean.
2. Preserve the resume's overall structure and section count.
3. The resume MUST still fit 2 pages.
4. Update tailoring_rationale to explain what changed and why, including any genuine \
gaps acknowledged.

## Output Format
Respond with ONLY valid JSON (no markdown fences) using the exact same schema as the \
input resume. Output the COMPLETE resume, not just the changed parts."""


def build_refiner_prompt(
    resume_json: str,
    critique_json: str,
    profile_text: str,
    job_text: str,
    company_notes: str | None = None,
    company_research: dict | None = None,
) -> list[dict]:
    """Build the user message for the refiner pass."""
    parts = [
        "## Current Resume (to be improved)\n",
        f"```json\n{resume_json}\n```",
        "\n---\n",
        "## Recruiter Critique\n",
        f"```json\n{critique_json}\n```",
        "\n---\n",
        "## Full Candidate Profile & Accomplishments\n",
        profile_text,
        "\n---\n",
        "## Target Job Posting\n",
        job_text,
    ]

    if company_notes:
        parts.append(f"\n\n## Company Context\n{company_notes}")

    if company_research:
        from app.ai.company_research import format_research_for_refiner

        parts.append(f"\n\n{format_research_for_refiner(company_research)}")

    parts.append("""

---

Address each weakness from the critique by revising the resume. For each weakness:
- If fix_type is "reframe": rewrite the relevant bullet(s) to better emphasize the \
job-relevant angle, using only facts from the profile.
- If fix_type is "add_from_profile": incorporate the referenced profile material, \
replacing a weaker bullet if needed to stay within length limits.
- If fix_type is "strengthen": sharpen the bullet with a stronger metric or outcome \
from the accomplishment's quantitative_specifics.
- If fix_type is "genuine_gap": do NOT fabricate. Reframe adjacent experience if \
possible, or strengthen other areas. Note the gap in tailoring_rationale.
- If category is "stiff_writing": COMPLETELY REWRITE the flagged section. For research \
descriptions, write clear specific prose — not a feature list. For summaries, write a \
specific argument for THIS role. For bullets, be punchy and outcome-led.
- If category is "redundancy": rewrite so the two sections complement each other — \
the research description tells the story, the bullet states the outcome.

Output the COMPLETE revised resume as valid JSON (no markdown fences) using the exact \
same schema as the input resume.""")

    return [{"role": "user", "content": "\n".join(parts)}]


# ---------------------------------------------------------------------------
# Research entry generation — for swapping individual selected_research items
# ---------------------------------------------------------------------------

RESEARCH_ENTRY_SYSTEM = f"""\
You generate a single selected_research entry for a tailored resume. \
You receive one accomplishment from the candidate's profile and the target job posting. \
Your job is to write a compelling 75-100 word description that highlights the most \
job-relevant aspects of this accomplishment, and a short category label.

## CRITICAL RULES
1. ONLY use facts from the provided accomplishment. NEVER fabricate metrics, outcomes, \
or skills not present in the accomplishment data.
2. The description should highlight aspects of the work most relevant to the target role.
3. Do NOT include sentences explaining why the work is relevant to the company — \
that is implicit from its presence on the resume.

{VOICE_RULES}

## Output Format
Respond with ONLY valid JSON (no markdown fences):
{{
  "category_label": "2-4 WORD LABEL",
  "title": "The accomplishment title (use exactly as provided)",
  "description": "75-100 words describing the work and its results",
  "accomplishment_id": "the accomplishment id (use exactly as provided)"
}}\
"""


def build_research_entry_prompt(
    accomplishment: dict,
    job_text: str,
) -> list[dict]:
    """Build the user message for generating a single research entry."""
    parts = [
        "## Accomplishment to Feature\n",
        f"ID: {accomplishment.get('id', 'unknown')}",
        f"Title: {accomplishment.get('title', '')}",
        f"Employer: {accomplishment.get('employer', '')}",
    ]
    if accomplishment.get("impact_summary"):
        parts.append(f"Impact: {accomplishment['impact_summary'].strip()}")
    quants = accomplishment.get("quantitative_specifics", [])
    if quants:
        parts.append(f"Metrics: {'; '.join(quants)}")
    so_what = accomplishment.get("so_what", "")
    if so_what:
        parts.append(f"So what: {so_what.strip()}")
    skills_demo = accomplishment.get("skills_demonstrated", [])
    if skills_demo:
        parts.append(f"Skills: {', '.join(skills_demo)}")

    parts.extend(
        [
            "\n---\n",
            "## Target Job Posting\n",
            job_text,
            "\n---\n",
            "Generate a selected_research entry for this accomplishment tailored to the above job. "
            "Use the accomplishment's title and id exactly as provided. "
            "Write a 2-4 word category_label that mirrors the job's domain language. "
            "Write a 2-3 sentence description highlighting the most job-relevant aspects.",
        ]
    )

    return [{"role": "user", "content": "\n".join(parts)}]


# ===========================================================================
# STAGED PIPELINE PROMPTS (v4)
#
# Generation flow:
#   strategic plan → selection → parallel(research entries + skill buckets)
#   → parallel(bullets per employer, with finalized research as input)
#   → summary + tagline (synthesizes everything)
#
# Each leaf prompt accepts a ``grounding_text`` parameter — the user's past
# hand-tuned versions for the same entity, used to ground tone and phrasing.
# ===========================================================================


# ---------------------------------------------------------------------------
# Stage [2]: Selection — picks which accomplishments to feature
# ---------------------------------------------------------------------------

SELECTION_SYSTEM = """\
You decide which accomplishments to feature on a tailored resume. You are NOT \
writing prose — you are picking IDs. Downstream stages will write the actual \
descriptions and bullets in parallel.

You receive a strategic plan (which already proposed picks) and the full \
accomplishment catalog. Confirm or adjust:

1. Pick exactly 3 accomplishments for the Selected Research section.
2. Pick 2-4 employers and the accomplishment IDs to feature in each employer's \
bullets. An accomplishment may appear in both research and a bullet — that's \
fine; downstream prompts handle the differentiation.
3. Give each employer a target bullet count: 4-5 for the headline employer, \
2-3 for the next, 2-3 for any others. Skip employers whose work doesn't \
strengthen the case for THIS role.

## Output
Respond with ONLY valid JSON (no markdown fences):
{
  "selected_research_accomplishment_ids": ["id1", "id2", "id3"],
  "experience_employer_anchors": [
    {"employer_key": "...", "accomplishment_ids": ["id1", "id2"], "bullet_count": 4},
    {"employer_key": "...", "accomplishment_ids": ["id3"], "bullet_count": 2}
  ]
}\
"""


def build_selection_prompt(
    plan_text: str,
    compact_profile: str,
    job_text: str,
) -> list[dict]:
    return [
        {
            "role": "user",
            "content": f"""\
## Strategic Plan
{plan_text}

## Candidate Profile (compact)
{compact_profile}

## Target Job
{job_text}

---

Pick the accomplishment IDs and employer anchors. Output valid JSON.
- Cross-check that each ID exists in the catalog.
- Use the employer ``key`` field exactly as it appears in the work history above.""",
        }
    ]


# ---------------------------------------------------------------------------
# Stage [3a]: Research entry (one per pick) — extends existing prompt with
# strategic plan + grounding from past versions.
# ---------------------------------------------------------------------------

_RESEARCH_ENTRY_V4_HEAD = """\
You generate ONE selected_research entry for a tailored resume.

Write a 75-100 word paragraph that sounds like a confident person \
describing their most interesting work to a smart colleague. The #1 \
priority is that it sounds NATURAL — something a human would actually \
write about their own work. No staccato fragments. No metric inflation.

## Ground rules
- Only use facts from the provided accomplishment. Never fabricate.
- Aim for ~75 words; 60-100 is fine. Don't pad with abstract phrasing \
("the work mattered because…", hedged claims, stacked noun phrases) to \
hit a floor. If the natural concrete version is 60 words, ship that — \
60 concrete words beat 90 padded ones.
- Open with what's different now because the work exists. Lead with an \
active, verb-led construction (Built, Designed, Replaced, Shipped, \
Discovered, Automated, Eliminated, Reimagined). Don't flip into passive \
transformation framings like "X moved from Y to Z" or "the project \
turned A into B."
- Earn technical credibility somewhere in the next sentence or two: how \
it was approached, what was non-trivial, how it was validated. No \
prescribed sentence order — write what flows.
- Do NOT copy impact_summary verbatim from the catalog. Rewrite completely.
"""

_RESEARCH_ENTRY_V4_TAIL = """\
## ⚠️ Voice — the ★ Most recent past version is your PRIMARY TARGET
This is the dominant rule. If a "Your accepted versions of this content" \
block appears in the user message, the entry marked ★ Most recent is the \
structural target your output should resemble. Your job is RETARGETING \
EMPHASIS for the new role, not rewriting voice:

- MIRROR the opening verb. If the most-recent version starts with "Built", \
yours starts with "Built" (or a tight Built-family verb like "Designed", \
"Shipped", "Engineered"). Do NOT switch to passive transformation framings \
like "X moved from Y to Z", "Y was designed", "the project turned A into B".
- Mirror sentence count, length, and clause rhythm — same number of sentences, \
same shape.
- Mirror metric framing. If past version says "matched trained human coders \
(kappa 0.71)", don't strip the parenthetical or invert the framing. If it \
quotes "450+ researchers across 700+ projects", yours uses the same shape.
- Mirror first-person posture — implied first person ("Built X to do Y") vs \
explicit first person ("I designed X") — whichever the most-recent version uses.
- The ONLY thing you should adapt is which job-relevant aspects to foreground. \
If the target role and the past version's job were similar, your output may \
end up only slightly different from the most-recent version. That's the goal.

## Anti-redundancy with the other Selected Research entries
If the user message contains an "Other Selected Research Entries Already in \
This Resume" block, those entries are locked in. Take a distinctly different \
angle, but the Voice rule above OVERRIDES this where they conflict:

- Don't reuse recurring narrative framings or phrasing chunks ("The hard part \
was…", "What made this difficult…", "That gave us…", "Built X as a Y where \
the hard part was…"). If a chunk like that appears in a prior entry, yours \
must not use it or any close paraphrase. This rule is hard.
- Pick a different facet of the work — if a prior entry centered on validation \
rigor, lead this one with what shipped, who used it, or the problem that was \
unsolvable before.
- Opening verbs SHOULD differ across entries when possible, but if the Voice \
target requires "Built" and another entry already opens with "Built", mirror \
the past version. Voice match wins for opening-verb conflicts.

## ⚠️ Anti-redundancy with the experience bullets — different facet, \
SAME concreteness

The same accomplishment almost always appears as an experience bullet \
too. The bullet carries the punchy adoption + features ("Shipped MUSE, \
used by 400+ researchers across 600+ projects"). Your research \
description should land on a DIFFERENT facet of the same work — but \
without going abstract. Concrete-and-different beats abstract-and- \
significant every time.

Concretely:
- The bullet carries the system name + capability list + adoption count. \
Don't duplicate those. If the bullet says "Shipped MUSE, used by 400+ \
researchers", your description doesn't need "MUSE was adopted by 400+ \
researchers across 600+ projects".
- The facet to lean into is the MEASUREMENT and EVIDENCE story: what \
specifically was measured (Cohen's kappa against trained coders, expert \
agreement, IRR), against what baseline (trained human coders, expert \
elicitation, hand-coded ground truth), what the number was, what it \
proved. Name the specific thing, not the abstract notion of "validation \
rigor".
- BAD (abstract significance): "Designed the measurement approach around \
expert judgment, which meant defining task criteria and validation \
procedures carefully enough that performance claims would hold up in a \
high-stakes defense setting."
- GOOD (concrete evidence): "Measured each system against expert-graded \
task completions instead of generic benchmark scores, and used \
inter-rater agreement among program reviewers to set a defensible \
threshold for what counted as a passing model."
- If you find yourself reaching for "the work mattered because…", "the \
contribution matters for…", "made X enough to Y", or any of the \
abstract-subject / hedge-claim shapes in VOICE_RULES, STOP. Name the \
specific measurement, the specific baseline, the specific number.

## Output
Respond with ONLY valid JSON (no markdown fences):
{
  "category_label": "2-4 WORD LABEL (job-relevant, not internal framing)",
  "title": "the accomplishment title (use exactly as provided)",
  "description": "2-3 sentences (75-100 words)",
  "accomplishment_id": "the accomplishment id (use exactly as provided)"
}\
"""

RESEARCH_ENTRY_V4_SYSTEM = (
    _RESEARCH_ENTRY_V4_HEAD + "\n" + VOICE_RULES + "\n" + _RESEARCH_ENTRY_V4_TAIL
)


def build_research_entry_v4_prompt(
    accomplishment: dict,
    plan_text: str,
    plan_entry: dict | None,
    job_text: str,
    grounding_text: str = "",
    critic_notes: str = "",
    prior_entries: list[dict] | None = None,
) -> list[dict]:
    """Build user message for a single research entry in the staged pipeline.

    ``prior_entries`` is the other Selected Research entries already finalized
    for this resume. When non-empty, they're shown as anti-redundancy context
    so the model can take a distinctly different angle.
    """
    parts = ["## Accomplishment to Describe"]
    parts.append(f"ID: {accomplishment.get('id', '?')}")
    parts.append(f"Title: {accomplishment.get('title', '?')}")
    parts.append(f"Employer: {accomplishment.get('employer', '?')}")
    if accomplishment.get("impact_summary"):
        parts.append(f"Impact: {accomplishment['impact_summary'].strip()}")
    if accomplishment.get("quantitative_specifics"):
        parts.append(f"Metrics: {'; '.join(accomplishment['quantitative_specifics'])}")
    if accomplishment.get("so_what"):
        parts.append(f"So what: {accomplishment['so_what'].strip()}")
    if accomplishment.get("hands_on_work"):
        parts.append(f"Hands-on work: {accomplishment['hands_on_work'].strip()}")
    if accomplishment.get("skills_demonstrated"):
        parts.append(f"Skills: {', '.join(accomplishment['skills_demonstrated'])}")

    if plan_entry:
        parts.append("\n## Plan Guidance for This Accomplishment")
        parts.append(f"Framing angle: {plan_entry.get('framing_angle', '?')}")
        parts.append(
            f"Reserve for bullets: {plan_entry.get('reserve_for_bullets', 'nothing specific')}"
        )

    parts.append(f"\n## Strategic Plan\n{plan_text}")
    parts.append(f"\n## Target Job\n{job_text}")

    if prior_entries:
        parts.append(
            "\n## Other Selected Research Entries Already in This Resume"
            "\nThese descriptions are already written and locked in. Your entry "
            "must take a DISTINCTLY DIFFERENT angle — don't echo their opening "
            "verb, their narrative framing, or recurring phrasing chunks like "
            "\"The hard part was…\", \"What made this difficult…\", \"That "
            "gave us…\". Each entry should feel like a different facet of "
            "the candidate's work, not a variation on the same template."
        )
        for j, prior in enumerate(prior_entries, start=1):
            label = prior.get("category_label", "")
            title = prior.get("title", "")
            description = (prior.get("description") or "").strip()
            parts.append(
                f"\n### Entry {j}: {label} — {title}\n{description}"
            )

    if grounding_text:
        parts.append("\n" + grounding_text)

    if critic_notes:
        parts.append(
            "\n## Critic notes on the previous attempt — fix THESE specifically\n" + critic_notes
        )

    parts.append(
        "\n---\n"
        "Write the research description. Output valid JSON. Use the "
        "accomplishment's title and id exactly as provided. 75-100 words."
    )
    return [{"role": "user", "content": "\n".join(parts)}]


# ---------------------------------------------------------------------------
# Stage [3b]: Skill bucket (one per call)
# ---------------------------------------------------------------------------


def build_skill_bucket_system(profile_data: dict) -> str:
    """Build per-skill-bucket system prompt."""
    skills = profile_data.get("skills", {})
    all_skills = []
    for cat in ["technical", "communication", "tools"]:
        cat_skills = skills.get(cat, [])
        if isinstance(cat_skills, list):
            all_skills.extend(cat_skills)
        elif isinstance(cat_skills, str):
            all_skills.append(cat_skills)
    skills_whitelist = ", ".join(all_skills) if all_skills else "see profile"

    return f"""\
You generate ONE category of the technical_skills section for a tailored resume.

You receive: the bucket name (ai_systems / data_science / engineering), the \
strategic plan, the target job, and a list of skills the candidate actually has.

## Rules
- ONLY use skills from this whitelist: {skills_whitelist}
- Reorder to lead with skills most relevant to the target job.
- Output a comma-separated string. No bullets, no headers.
- 6-12 skills typical. Quality > quantity.

## Output
Respond with ONLY valid JSON (no markdown fences):
{{
  "text": "Skill1, Skill2, Skill3, ..."
}}\
"""


def build_skill_bucket_prompt(
    bucket_name: str,
    plan_text: str,
    job_text: str,
    grounding_text: str = "",
    critic_notes: str = "",
    other_buckets: dict[str, str] | None = None,
) -> list[dict]:
    parts = [
        f"## Bucket\n{bucket_name}",
        f"\n## Strategic Plan\n{plan_text}",
        f"\n## Target Job\n{job_text}",
    ]
    if other_buckets:
        parts.append(
            "\n## Other buckets in this resume (skills appearing here MUST NOT appear in your bucket)"
        )
        for name, value in other_buckets.items():
            if value:
                parts.append(f"- {name}: {value}")
    if grounding_text:
        parts.append("\n" + grounding_text)
    if critic_notes:
        parts.append(
            "\n## Critic notes on the previous attempt — fix THESE specifically\n" + critic_notes
        )
    parts.append(f"\n---\nWrite the {bucket_name} skill list. Output valid JSON.")
    return [{"role": "user", "content": "\n".join(parts)}]


# ---------------------------------------------------------------------------
# Stage [4]: Bullet set per employer — sees finalized research for dedup
# ---------------------------------------------------------------------------


def build_bullet_set_system(profile_data: dict) -> str:
    skills = profile_data.get("skills", {})
    all_skills = []
    for cat in ["technical", "communication", "tools"]:
        cat_skills = skills.get(cat, [])
        if isinstance(cat_skills, list):
            all_skills.extend(cat_skills)
    skills_whitelist = ", ".join(all_skills) if all_skills else "see profile"

    return f"""\
You write the experience bullets for ONE employer in a tailored resume.

Write like a human describing their own work to a smart colleague. The #1 \
priority is that every bullet sounds natural. Do not contort sentences to \
satisfy formatting rules — if a simple, clear sentence breaks a "rule," keep \
the simple sentence.

## Ground rules
- Only use facts from the provided accomplishments. Never fabricate.
- Skills whitelist for any tools mentioned: {skills_whitelist}
- ONE accomplishment per bullet. A bullet is typically 1-2 sentences (3 \
only if the work genuinely needs it).
- Lead with an active, verb-led construction (Shipped, Built, Cut, \
Replaced, Discovered, Designed, Improved). The CHANGE is the result of \
that action — describe it that way, not the other way around.
- Translate jargon metrics into plain language ("Cohen's kappa = 0.71" → \
"matched trained human coders (kappa 0.71)").
- Each bullet must include ``accomplishment_ids`` for audit.

## ⚠️ Voice — match the past versions when present
If a "Your past hand-tuned versions" block appears in the user message, those \
are the candidate's own prior bullet sets for THIS SAME employer. Treat them \
as the source of TRUTH for voice:

- MIRROR opening verb choices, sentence length, and rhythm. Past versions \
that start "Shipped X...", "Cut Y by Z%...", "Improved A to B..." establish \
the voice — yours must match.
- Do NOT use passive constructions ("X was shipped", "Y moved from A to B", \
"the workflow became...") if past versions are active. That's not the \
candidate's voice.
- Adapt CONTENT (which accomplishments to feature, which angle to take for \
this role) — do NOT rewrite STYLE.
- If past versions favor short bullets, yours should be short. If they favor \
1-2 longer sentences, match that.

## Cross-section coordination
You will receive the FINALIZED Selected Research entries above. If an \
accomplishment_id you're using also appears in research, your bullet MUST say \
something COMPLETELY DIFFERENT. Research = transformation story; bullet = \
punchy outcome or different angle. Read the research description first; do \
not paraphrase it.

{VOICE_RULES}

## Output
Respond with ONLY valid JSON (no markdown fences):
{{
  "bullets": [
    {{"text": "...", "accomplishment_ids": ["id1"]}},
    ...
  ]
}}\
"""


def build_bullet_set_prompt(
    employer_key_str: str,
    employer_name: str,
    bullet_count: int,
    accomplishments: list[dict],
    plan_mapping: dict | None,
    plan_text: str,
    finalized_research: list[dict],
    job_text: str,
    grounding_text: str = "",
    critic_notes: str = "",
) -> list[dict]:
    """Build user message for one employer's bullet set.

    ``finalized_research`` is the array of selected_research entries already
    produced by Stage [3a]. The LLM reads the description text directly to
    avoid duplicating it.
    """
    parts = [f"## Employer: {employer_name} (key: {employer_key_str})"]
    parts.append(f"Write {bullet_count} bullets for this employer.\n")

    parts.append("## Accomplishments Available for This Employer:")
    for a in accomplishments:
        aid = a.get("id", "?")
        parts.append(f"\n  ID: {aid}")
        parts.append(f"  Title: {a.get('title', '?')}")
        if a.get("impact_summary"):
            parts.append(f"  Impact: {a['impact_summary'].strip()[:300]}")
        if a.get("quantitative_specifics"):
            parts.append(f"  Metrics: {'; '.join(a['quantitative_specifics'])}")
        if a.get("hands_on_work"):
            parts.append(f"  Hands-on: {a['hands_on_work'].strip()[:300]}")
        if a.get("so_what"):
            parts.append(f"  So what: {a['so_what'].strip()[:200]}")

    if plan_mapping:
        parts.append(f"\n## Plan Guidance for {employer_name}")
        if plan_mapping.get("emphasis"):
            parts.append(f"Emphasis: {plan_mapping['emphasis']}")
        if plan_mapping.get("rationale"):
            parts.append(f"Rationale: {plan_mapping['rationale']}")

    parts.append(f"\n## Strategic Plan\n{plan_text}")

    # Finalized research: included so the LLM can de-dupe against it.
    if finalized_research:
        parts.append("\n## FINALIZED Selected Research (do NOT repeat this content)")
        for entry in finalized_research:
            aid = entry.get("accomplishment_id", "?")
            parts.append(
                f"\n- accomplishment_id={aid}\n"
                f"  title: {entry.get('title', '')}\n"
                f"  description: {entry.get('description', '')}"
            )
        parts.append(
            "\nFor any bullet whose accomplishment_id appears above, take a "
            "different angle (punchy outcome, scale figure, technical method) "
            "rather than restating the transformation story."
        )

    parts.append(f"\n## Target Job\n{job_text}")

    if grounding_text:
        parts.append("\n" + grounding_text)

    if critic_notes:
        parts.append(
            "\n## Critic notes on the previous attempt — fix THESE specifically\n" + critic_notes
        )

    parts.append(
        f"\n---\n"
        f"Write {bullet_count} bullets for {employer_name}. Output valid JSON. "
        f"Each bullet must include accomplishment_ids."
    )
    return [{"role": "user", "content": "\n".join(parts)}]


# ---------------------------------------------------------------------------
# Stage [5]: Summary + tagline — written last, sees the assembled draft
# ---------------------------------------------------------------------------

_SUMMARY_TAGLINE_HEAD = """\
You write the SUMMARY and TAGLINE for a tailored resume — the final pieces, \
after all other content has been generated. They are the connective tissue: \
they should reflect the case the rest of the resume actually makes.

## tagline
3-4 keywords separated by " · ". Mirror the target job's domain language. If a \
`Role lane` is named in the user message, lead with the lane's vocabulary:
  - evals_safety → lead with "AI Evaluation"
  - human_data → lead with "Human-AI Systems" or "Human Data Workflows"
  - applied_ai_eng → lead with "Applied AI" or "LLM Systems"
  - research_engineer → lead with "Research Engineering"

## summary
2-4 sentences, 50-80 words. Makes the hiring manager think "I need to meet \
this person."

Rules:
- No company names. No metrics. No disclaimers.
- Each sentence must parse independently. Do NOT merge unrelated ideas.
- Does NOT start with "Research scientist with N years..." — that's the most \
boring opening possible.
- Make an ARGUMENT, not a list.
- If a `Role lane` is named in the user message, the summary's vocabulary \
should sit naturally inside that lane — describe the work the candidate \
actually does using the lane's framing. Lane vocabulary cues:
  - evals_safety: "eval infrastructure", "model behavior", "human feedback", \
    "benchmark design", "expert validation"
  - human_data: "annotation workflows", "expert feedback loops", \
    "dataset quality", "scalable labeling", "human-in-the-loop"
  - applied_ai_eng: "LLM applications", "agentic workflows", "production AI"
  - research_engineer: "research infrastructure", "eval harnesses", \
    "experimentation tooling"
Weave these into how the work is described. Do NOT add a separate \
"Strongest fit on teams that...", "Best for teams building...", or \
"Now focused on..." closing sentence keyed off the lane — that's the \
meta-pitch shape banned below and the #1 way the model leaks the prompt.
- **No meta-pitch / interview-pitch language.** Do NOT write FROM THE \
PERSPECTIVE OF A RECRUITER. The summary describes the candidate's work; \
it does NOT instruct the reader how to evaluate the candidate. Banned \
phrase shapes — these are HARD bans, including subtle variants: \
"Strongest fit on teams...", "Strong interview case for...", "Brings \
exactly what teams need when...", "An ideal candidate for...", "Hiring \
managers will appreciate...", "Best suited for teams that...", "Best \
for teams building...". If your draft contains any phrase that names \
the kind of team this person would join, delete that sentence. The \
reader draws the fit conclusion from the work description itself.

Good: "Builds AI tools that domain experts actually adopt — the last one \
replaced manual research workflows across a 2,000-person organization and \
matched expert-level accuracy."

Bad: "Technical leader who turns ambiguous AI research into execution plans, \
evaluation workflows, and shipped systems." (Generic, capability list.)

Bad: "Strong interview case for teams that need an engineer who can connect \
sensor behavior, reliable data systems, and practical tools for technical \
users." (Meta-pitch — instructs the reader, doesn't describe the work.)
"""

_SUMMARY_TAGLINE_TAIL = """\
## Output
Respond with ONLY valid JSON (no markdown fences):
{
  "tagline": "Keyword · Keyword · Keyword",
  "summary": "2-4 sentences, 50-80 words"
}\
"""

SUMMARY_TAGLINE_SYSTEM = (
    _SUMMARY_TAGLINE_HEAD + "\n" + VOICE_RULES + "\n" + _SUMMARY_TAGLINE_TAIL
)


# ---------------------------------------------------------------------------
# Stage [5]: Critic — finds specific, fixable issues per entity
# ---------------------------------------------------------------------------

CRITIC_V4_SYSTEM = """\
You audit a tailored resume draft and flag SPECIFIC, FIXABLE issues per entity.
You are not allowed to rewrite anything. You produce a list of targeted notes
that a refiner stage will use to re-run individual entities.

You receive: the assembled draft (research + skills + bullets), the candidate's
past hand-tuned versions for those entities (grounding), the strategic plan, and
the target job. The candidate has already invested editing effort into the
grounding examples — those are the source of truth for voice and taste.

## What to flag

Only flag issues that pass this bar: a refiner running on this single entity,
with the note appended, would clearly improve the output. If you can't say
WHAT to do differently, don't flag it.

Flag categories:

1. **voice** — opening verb / sentence structure / passive vs active doesn't
   match the entity's grounding examples. (e.g. grounding starts with
   "Designed and built" but new draft says "Qualitative research moved from".)
2. **skills_overlap** — the same skill appears in multiple buckets
   (ai_systems / data_science / engineering). The candidate's taste: each
   skill belongs in EXACTLY ONE bucket. Special cases:
     - Python, SQL → data_science only.
     - LLM eval, RAG, agent tooling, Cursor, Codex, Claude Code, Langfuse → ai_systems only.
     - FastAPI, Postgres, Docker, Git, REST API design → engineering only.
   Flag the bucket(s) where a skill appears wrongly.
3. **bucket_purity** — ai_systems contains a non-AI-specific tool (e.g. Python,
   SQL, REST API design). engineering contains stats/ML methods. data_science
   contains pure infra (Docker, FastAPI). Flag the offending bucket.
4. **redundancy** — a research description and a bullet for the same
   accomplishment_id say the same thing.
5. **passive_opening** — a research description or bullet leads with a
   passive construction ("X was designed", "Y moved from A to B",
   "the project turned A into B"). Voice should be active and verb-led.
6. **metric_inflation** — fabricated or product-of-products metrics
   ("4.5M model decisions" derived from 300K * 15).
7. **buzzword_stack** — the entity either (a) echoes insider jargon from the
   underlying accomplishment data ("instantiated", "operationalized",
   "digital clone", "framework", "language-sensitive simulation") instead
   of translating it to plain English, or (b) stacks 2+ modifiers on a
   single noun ("language-sensitive simulation framework", "AI-powered
   agent-based misinformation engine"). Each extra modifier roughly halves
   how many readers parse the phrase on first read. In `direction`, name
   the specific offending noun phrase and tell the refiner to break it
   across clauses or drop modifiers — don't just say "simplify".
8. **other** — only if it's specific and fixable.

## Output

Respond with ONLY valid JSON (no markdown fences):
{
  "issues": [
    {
      "target": "research[0]" | "research[1]" | "research[2]"
              | "skills.ai_systems" | "skills.data_science" | "skills.engineering"
              | "experience.<employer_key>",
      "kind": "voice" | "skills_overlap" | "bucket_purity" | "redundancy" | "passive_opening" | "metric_inflation" | "buzzword_stack" | "other",
      "current": "the offending text snippet (one short quote)",
      "issue": "what's specifically wrong (one sentence)",
      "direction": "what the refiner should do INSTEAD (one sentence — not a full rewrite)"
    }
  ]
}

Empty list if nothing is worth fixing. Do NOT pad with marginal nits.\
"""


def build_critic_v4_prompt(
    plan_text: str,
    assembled_draft: str,
    grounding_summary: str,
    job_text: str,
) -> list[dict]:
    return [
        {
            "role": "user",
            "content": f"""\
## Strategic Plan
{plan_text}

## Current Draft
{assembled_draft}

## Past hand-tuned versions (grounding by entity)
{grounding_summary or "(none — no past versions available for the chosen entities)"}

## Target Job
{job_text}

---

Audit the draft. Flag specific, fixable issues per entity. Output valid JSON.""",
        }
    ]


PUBLICATIONS_SYSTEM = """\
You select publications for a tailored resume's Selected Publications section.

You receive the candidate's full publication list and the target job. Pick 2-3 \
publications for industry/engineering roles, up to 5 for research-focused \
roles. Prefer first-author and topic-aligned. Fewer high-relevance picks beat \
a longer list.

## Output
Respond with ONLY valid JSON (no markdown fences):
{
  "publications": [
    {"citation": "Authors. \\"Title.\\" Venue, Year.", "publication_id": "id-from-list"}
  ]
}\
"""


def build_publications_prompt(
    publications_text: str,
    plan_text: str,
    job_text: str,
) -> list[dict]:
    return [
        {
            "role": "user",
            "content": f"""\
## Available Publications
{publications_text}

## Strategic Plan
{plan_text}

## Target Job
{job_text}

---

Pick the most job-relevant publications. Use the publication_id exactly as \
provided. Format the citation as "Authors. \\"Title.\\" Venue, Year." Output \
valid JSON.""",
        }
    ]


def build_summary_tagline_prompt(
    plan_text: str,
    assembled_draft: str,
    job_text: str,
    grounding_text: str = "",
    role_lane: str = "",
) -> list[dict]:
    parts = [
        "## Strategic Plan",
        plan_text,
    ]
    if role_lane:
        parts.append(f"\n## Role lane\n{role_lane}")
    parts.extend(
        [
            "\n## Resume Content (everything else, already finalized)",
            assembled_draft,
            "\n## Target Job",
            job_text,
        ]
    )
    if grounding_text:
        parts.append("\n" + grounding_text)
    parts.append(
        "\n---\n"
        "Write the tagline and summary. They should synthesize the resume "
        "content into a cohesive argument for why this person should get an "
        "interview. Do NOT repeat specific bullets — the summary makes the "
        "CASE, the bullets provide the EVIDENCE.\n\n"
        "BEFORE YOU OUTPUT — read the summary aloud. Does it sound like a "
        "confident colleague vouching for someone, or like a template? "
        "50-80 words. No pronouns. Output valid JSON."
    )
    return [{"role": "user", "content": "\n".join(parts)}]
