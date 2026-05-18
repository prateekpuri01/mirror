"""LLM-powered profile extraction from resume text and web sources."""

import json
import logging

from app.ai.client import EXTRACTION_MODEL, RESUME_MODEL, get_openai_client
from app.ai.keyword_generator import _strip_fences

logger = logging.getLogger(__name__)

_RESUME_EXTRACTION_SYSTEM = """\
You are a resume parsing assistant. Given raw text extracted from a PDF/DOCX resume, \
extract structured profile data.

Your output must be valid JSON matching this exact schema:
{
  "personal": {
    "name": "Full Name",
    "email": "email@example.com or null",
    "phone": "phone number or null",
    "location": "City, State or null",
    "linkedin": "linkedin URL if found or null",
    "google_scholar": "scholar URL if found or null"
  },
  "target_roles": [
    {"title": "inferred job title", "seniority": "senior"}
  ],
  "domains": ["domain1", "domain2"],
  "skills": {
    "technical": ["skill1", "skill2"],
    "communication": ["skill1"],
    "tools": ["tool1", "tool2"]
  },
  "education": [
    {"degree": "PhD", "field": "Field", "institution": "University", "year": "2019", "honors": "if any or null"}
  ],
  "experience_years": "7",
  "work_history": [
    {"employer": "Company", "title": "Job Title", "start": "2022-06", "end": null, "location": "City, State"}
  ],
  "awards": ["Award 1", "Award 2"]
}

Rules:
- Extract ONLY what is explicitly stated in the resume text. Do not fabricate information.
- For target_roles, infer 2-4 roles based on the person's most recent experience and career trajectory.
- For seniority, infer from years of experience and title history: junior|mid|senior|staff|principal|lead|director.
- For domains, extract 3-8 professional domains from the resume content.
- For skills.technical, extract HARD technical skills only: programming languages, \
  statistical methods, ML techniques, algorithms, scientific methods, engineering \
  methodologies. NOT soft skills, NOT writing, NOT collaboration.
- For skills.communication, extract writing, presentation, teaching, stakeholder \
  engagement, interpersonal, and collaboration skills. Things like "technical writing", \
  "cross-team collaboration", "communicating technical concepts", "science communication" \
  belong HERE, not in technical.
- For skills.tools, extract specific named software, platforms, libraries, and \
  technologies (e.g. Python, Docker, Tableau, AWS). NOT generic skills — only \
  things you can install, open, or log into.

CRITICAL rules for work_history:
- Extract EVERY distinct employer or position, including research positions, internships, \
  fellowships, and undergraduate roles. Do NOT only list the most recent jobs.
- Many resumes list experience bullets without clear employer headings. Use context clues \
  in the bullets themselves (company names mentioned, project context, award references) \
  and the objective/summary section to infer which employer each group of bullets belongs to.
- Use date clues from awards, fellowships, and other sections to infer approximate dates \
  when explicit dates are missing. For example, if an award says "2020-2021" for a company, \
  use that to estimate the employment period.
- Use YYYY-MM format for dates. Use null for "end" if current/present. Use null only if \
  truly unknowable — prefer approximate dates over null.
- Order work_history from most recent to oldest.

- For education, look carefully for institution names, degree types, and years. These are \
  often in a separate section or inferred from context (e.g., fellowship at a university).
- experience_years: count total years of professional work experience (not education).
- Output ONLY valid JSON, no markdown fences."""

_PATCH_SYSTEM = """\
You are a profile enrichment assistant. You are given a structured profile that was \
already extracted from a resume, plus additional text from the user's online profiles \
(LinkedIn, GitHub, personal website, etc.).

Your job is to PATCH the existing profile by filling in missing or null fields using \
information from the online profile text. Do NOT regenerate or replace fields that \
already have good values.

Your output must be valid JSON with ONLY the fields that should be updated:
{
  "personal": {"location": "Washington, DC"},
  "work_history": [...],
  "skills": {"technical": [...], "communication": [...], "tools": [...]},
  "domains": [...],
  "education": [...]
}

Rules:
- ONLY include fields that are missing, null, empty, or clearly incomplete in the original.
- For work_history: fill in missing start/end dates, titles, or locations. Add positions \
  that appear in LinkedIn but are missing from the resume. Do NOT remove existing entries.
- For skills: add skills mentioned in the online profiles that are missing from the \
  existing lists. Do NOT remove existing skills.
- For domains: add domains evident from online profiles. Do NOT remove existing ones.
- For education: fill in missing institution names, years, or honors.
- For personal: fill in missing location, phone, or links.
- Do NOT include fields that are already well-populated.
- Do NOT generate accomplishments or publications — those are handled separately.
- Output ONLY valid JSON, no markdown fences. Output an empty object {} if nothing needs updating."""


async def extract_profile_from_resume(resume_text: str) -> dict:
    """Extract structured profile data from raw resume text using LLM.

    Returns a dict matching the ProfileData schema.
    Raises RuntimeError on LLM/parse failure.
    """
    client = get_openai_client()
    response = await client.chat.completions.create(
        model=RESUME_MODEL,
        messages=[
            {"role": "system", "content": _RESUME_EXTRACTION_SYSTEM},
            {"role": "user", "content": f"Resume text:\n---\n{resume_text[:15000]}\n---"},
        ],
        max_completion_tokens=6000,
    )

    choice = response.choices[0]
    raw = choice.message.content or ""
    logger.info(
        "Base profile extraction: finish_reason=%s, raw_length=%d",
        choice.finish_reason,
        len(raw),
    )
    raw = _strip_fences(raw)

    try:
        profile = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "Failed to parse resume extraction (finish=%s): %s",
            choice.finish_reason,
            raw[:500],
        )
        raise RuntimeError("Failed to parse resume. Please try again.")

    if not isinstance(profile, dict):
        raise RuntimeError("Failed to parse resume. Please try again.")

    # Generate work_history keys
    for wh in profile.get("work_history", []):
        if "key" not in wh:
            employer = wh.get("employer") or ""
            wh["key"] = employer.lower().replace(" ", "_").replace("—", "_").replace("-", "_")

    return profile


_COMPLETE_PROFILE_EXTRACTION_SYSTEM = """\
You are a career profile assistant. Given raw resume text and a structured profile \
extracted from it, generate accomplishment and publication entries.

Your output must be valid JSON matching this schema:
{
  "accomplishments": [
    {
      "id": "resume-<employer_slug>-<short_slug>",
      "category": "project",
      "title": "Short project/accomplishment title",
      "employer": "Company Name",
      "work_history_key": "employer_slug",
      "date_range": "2020-2022",
      "impact_summary": "1-3 sentences describing what was done and why it mattered",
      "quantitative_specifics": ["metric or result 1", "metric or result 2"],
      "so_what": "Why this matters for future roles — frame for a hiring manager",
      "skills_demonstrated": ["skill1", "skill2"],
      "relevance_weight": 0.7,
      "tags": ["tag1", "tag2"]
    }
  ],
  "publications": [
    {
      "id": "resume-pub-<short_slug>",
      "title": "Full publication title",
      "authors": ["Author One", "Author Two"],
      "venue": "Journal or Conference Name",
      "year": "2023",
      "type": "journal",
      "url": "URL if mentioned or null",
      "doi": "DOI if mentioned or null",
      "abstract": "Brief description if available or null",
      "first_author": true,
      "impact_summary": "What this paper contributes",
      "so_what": "Why this matters for the candidate's career",
      "skills_demonstrated": ["skill1", "skill2"],
      "relevance_weight": 0.6
    }
  ]
}

CRITICAL rules for accomplishments:
- Each accomplishment must be about a SPECIFIC, NAMED project, system, tool, initiative, \
  study, or deliverable mentioned in the resume — NOT a vague category of work.
  GOOD: "MUSE: LLM-Assisted Qualitative Research System"
  GOOD: "Fraud Detection Model for Options Trading Surveillance"
  BAD: "AI Policy Research" (too vague — which specific project?)
  BAD: "Deep-learning fraud detection systems" (which system specifically?)
- If the resume mentions a project/system/tool by name, use that exact name in the title.
- If the resume describes a specific initiative without naming it, create a descriptive \
  title referencing the concrete deliverable (e.g. "Real-Time Market Manipulation Dashboard").
- Only fall back to general descriptions if the resume bullet truly has no specifics.
- Each bullet point or distinct piece of work in the resume should be its OWN accomplishment \
  entry — do not merge multiple projects into one vague entry.

CRITICAL rules for employer attribution:
- Use the work_history_key values provided. ONLY use keys from the list.
- Many resumes list bullets without clear employer headings. Use context clues within each \
  bullet (e.g., "FINRA", "RAND", "regulators" → FINRA, "counseling conversations" → \
  Crisis Text Line, "farmers market" → likely an internship/fellowship) to determine the \
  correct employer.
- Do NOT assign bullets to the wrong employer just because it's the closest match. If a \
  bullet clearly describes work at a different organization than the available keys, use \
  the BEST matching key or flag it with the most reasonable employer.
- PhD research, undergraduate research, and internship work should be attributed to the \
  university or program, NOT to a professional employer.

Publication rules:
{pub_instruction}
- skills_demonstrated: use skills from the extracted profile's skill lists.
- quantitative_specifics: extract any numbers, metrics, percentages, dollar amounts, \
  user counts, etc. If none are stated, use an empty array — do NOT fabricate metrics.
- relevance_weight: 0.9+ for flagship work, 0.5-0.8 for solid contributions, <0.5 for minor items.
- Output ONLY valid JSON, no markdown fences."""


_PUB_INSTRUCTION_SKIP = """\
- Return an EMPTY publications array — publications will be imported separately \
  from Google Scholar via a dedicated pipeline."""

_PUB_INSTRUCTION_EXTRACT = """\
- Extract ALL publications, papers, patents, or research outputs mentioned in the resume.
- Each publication should be a separate entry with full title, authors if listed, venue, year.
- If the resume only mentions publications in passing (e.g. "published four articles in \
  Science and Nature Chemistry"), create individual entries for each distinct publication \
  you can identify, using the details available. If you cannot distinguish individual \
  papers, create one entry per distinct venue/topic mentioned.
- If no publications are mentioned, return an empty publications array."""


async def extract_complete_profile_from_resume(
    resume_text: str, extracted_profile: dict, has_scholar_url: bool = False
) -> dict:
    """Extract accomplishments and publications from resume text.

    Args:
        resume_text: Raw text from the resume.
        extracted_profile: The base profile already extracted (for context).
        has_scholar_url: If True, skip publication extraction (Scholar pipeline handles it).

    Returns:
        Dict with "accomplishments" and "publications" lists.
    """
    work_history = extracted_profile.get("work_history", [])
    wh_context = "\n".join(
        f"- key={wh.get('key', '')}: {wh.get('employer', '')} — {wh.get('title', '')} "
        f"({wh.get('start', '')}-{wh.get('end', 'present')})"
        for wh in work_history
    )

    from app.ai.skill_utils import flatten_skills
    all_skills = flatten_skills(extracted_profile.get("skills", {}))
    target_roles = [r.get("title", "") for r in extracted_profile.get("target_roles", [])]

    pub_instruction = _PUB_INSTRUCTION_SKIP if has_scholar_url else _PUB_INSTRUCTION_EXTRACT
    system_prompt = _COMPLETE_PROFILE_EXTRACTION_SYSTEM.replace(
        "{pub_instruction}", pub_instruction
    )

    user_content = f"""Resume text:
---
{resume_text[:12000]}
---

Work history (use these keys for work_history_key):
{wh_context}

Skills: {', '.join(all_skills[:40])}
Target roles: {', '.join(target_roles)}

Extract all accomplishments{'' if has_scholar_url else ' and publications'} from this resume."""

    client = get_openai_client()
    response = await client.chat.completions.create(
        model=RESUME_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        max_completion_tokens=12000,
    )

    choice = response.choices[0]
    raw = choice.message.content or ""
    logger.info(
        "Complete profile extraction: finish_reason=%s, raw_length=%d",
        choice.finish_reason,
        len(raw),
    )
    raw = _strip_fences(raw)

    try:
        complete = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "Failed to parse complete profile extraction (finish=%s): %s",
            choice.finish_reason,
            raw[:500],
        )
        return {"accomplishments": [], "publications": []}

    if not isinstance(complete, dict):
        return {"accomplishments": [], "publications": []}

    # Tag as auto-populated
    for acc in complete.get("accomplishments", []):
        acc["auto_populated"] = True
        acc["source"] = "onboarding"
    for pub in complete.get("publications", []):
        pub["auto_populated"] = True

    return complete


async def assemble_profile_from_sources(
    resume_text: str,
    resume_extracted: dict,
    url_texts: dict[str, str],
) -> tuple[dict, dict]:
    """Enrich an already-extracted profile with data from crawled URLs.

    Two modes:
    - If resume_extracted has meaningful data (resume was uploaded): use URL text
      to PATCH gaps in the existing profile. Accomplishments are NOT regenerated.
    - If resume_extracted is empty (no resume, only URLs): treat the richest URL
      text as a resume and run the full extraction pipeline on it.

    Returns (profile_dict, complete_profile_dict).
    """
    if not url_texts:
        return resume_extracted, {"accomplishments": [], "publications": []}

    # Check if we have a meaningful resume extraction already
    has_resume = bool(
        resume_extracted.get("work_history")
        or resume_extracted.get("personal", {}).get("name")
    )

    if not has_resume:
        # No resume — use the longest URL text as a pseudo-resume
        best_url = max(url_texts, key=lambda u: len(url_texts[u]))
        best_text = url_texts[best_url]
        logger.info("No resume provided; using %s (%d chars) as primary source", best_url, len(best_text))

        profile = await extract_profile_from_resume(best_text)
        has_scholar = bool((profile.get("personal") or {}).get("google_scholar"))
        complete = await extract_complete_profile_from_resume(
            best_text, profile, has_scholar_url=has_scholar
        )
        return profile, complete

    # Resume exists — do additive-only patching with URL data
    url_context = "\n\n".join(
        f"--- Source: {url} ---\n{text[:5000]}"
        for url, text in url_texts.items()
        if text
    )

    user_content = f"""Current profile (already extracted from resume):
---
{json.dumps(resume_extracted, indent=2)[:6000]}
---

Additional online profile text:
---
{url_context}
---

Identify fields that are missing, null, or incomplete in the current profile and \
output ONLY the patches needed. If nothing needs updating, output {{}}."""

    client = get_openai_client()
    response = await client.chat.completions.create(
        model=EXTRACTION_MODEL,
        messages=[
            {"role": "system", "content": _PATCH_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        max_completion_tokens=4000,
    )

    choice = response.choices[0]
    raw = choice.message.content or ""
    logger.info("Profile patch: finish_reason=%s, raw_length=%d", choice.finish_reason, len(raw))
    raw = _strip_fences(raw)

    try:
        patches = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Failed to parse profile patches, using resume extraction as-is")
        return resume_extracted, {"accomplishments": [], "publications": []}

    if not isinstance(patches, dict) or not patches:
        return resume_extracted, {"accomplishments": [], "publications": []}

    # Apply patches to the existing profile
    patched = dict(resume_extracted)
    for key, value in patches.items():
        if key in ("accomplishments", "publications", "complete_profile"):
            continue  # Never patch these via assembly

        existing = patched.get(key)

        if isinstance(value, list) and isinstance(existing, list):
            # For lists (work_history, education, skills, domains, awards):
            # merge new items, don't replace
            if key == "work_history":
                for wh in value:
                    new_emp = (wh.get("employer") or "").lower().strip()
                    new_title = (wh.get("title") or "").lower().strip()

                    # Skip entries with no identifying info
                    if not new_emp and not new_title:
                        # Try to match by date to fill gaps on existing entries
                        new_start = wh.get("start", "")
                        for ew in existing:
                            ew_start = ew.get("start", "")
                            if new_start and ew_start and new_start[:4] == ew_start[:4]:
                                for field in ("start", "end", "location"):
                                    if not ew.get(field) and wh.get(field):
                                        ew[field] = wh[field]
                                break
                        continue

                    # Try to match to existing entry (employer or title substring)
                    matched = False
                    for ew in existing:
                        ex_emp = (ew.get("employer") or "").lower()
                        ex_title = (ew.get("title") or "").lower()
                        emp_match = (
                            (new_emp and ex_emp and (new_emp in ex_emp or ex_emp in new_emp))
                            or new_emp == ex_emp
                        )
                        title_match = (
                            (new_title and ex_title and (new_title in ex_title or ex_title in new_title))
                            or new_title == ex_title
                        )
                        if emp_match or (title_match and new_title):
                            # Fill missing fields on existing entry
                            for field in ("start", "end", "location", "title"):
                                if not ew.get(field) and wh.get(field):
                                    ew[field] = wh[field]
                            matched = True
                            break

                    if not matched and new_emp:
                        # Genuinely new position — add it
                        if "key" not in wh:
                            wh["key"] = new_emp.replace(" ", "_").replace("—", "_").replace("-", "_")
                        existing.append(wh)
                patched[key] = existing
            elif key in ("domains", "awards"):
                # Dedupe by lowercase
                existing_lower = {s.lower() for s in existing}
                for item in value:
                    if item.lower() not in existing_lower:
                        existing.append(item)
                patched[key] = existing
            elif key == "education":
                for ed in value:
                    new_deg = (ed.get("degree") or "").lower().strip()
                    new_field = (ed.get("field") or "").lower().strip()
                    new_inst = (ed.get("institution") or "").lower().strip()

                    # Try to match to existing entry (degree type or institution)
                    matched = False
                    for ee in existing:
                        ex_deg = (ee.get("degree") or "").lower()
                        ex_field = (ee.get("field") or "").lower()
                        ex_inst = (ee.get("institution") or "").lower()
                        deg_match = new_deg and ex_deg and (new_deg in ex_deg or ex_deg in new_deg)
                        field_match = new_field and ex_field and (new_field in ex_field or ex_field in new_field)
                        inst_match = new_inst and ex_inst and (new_inst in ex_inst or ex_inst in new_inst)
                        if deg_match and (field_match or inst_match or not new_field):
                            for field in ("institution", "year", "honors", "field"):
                                if not ee.get(field) and ed.get(field):
                                    ee[field] = ed[field]
                            matched = True
                            break
                    if not matched and (new_deg or new_inst):
                        existing.append(ed)
                patched[key] = existing
            else:
                patched[key] = existing  # Keep existing for unknown list fields
        elif isinstance(value, dict) and isinstance(existing, dict):
            # For dicts (personal, skills, search_preferences): fill missing keys
            merged = dict(existing)
            if key == "skills":
                # Merge skill lists additively
                for subkey in ("technical", "communication", "tools"):
                    existing_skills = set(s.lower() for s in merged.get(subkey, []))
                    for skill in value.get(subkey, []):
                        if skill.lower() not in existing_skills:
                            merged.setdefault(subkey, []).append(skill)
            else:
                for subkey, subval in value.items():
                    if not merged.get(subkey) and subval:
                        merged[subkey] = subval
            patched[key] = merged
        elif not existing and value:
            # Field was empty/null, fill it
            patched[key] = value

    # Accomplishments are preserved from resume extraction — not touched here
    return patched, {"accomplishments": [], "publications": []}
