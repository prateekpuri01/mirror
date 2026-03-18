"""Layer 2: Deterministic batch cleaning for job titles and company names.

Applies regex-based rules to fix common parsing artifacts (URL fragments,
YC batch tags, domain suffixes, generic titles, etc.).  Idempotent — only
touches rows where clean_* is NULL and the raw value looks dirty.
"""

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Company, Job

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Generic / junk title patterns (titles that aren't real job titles)
# ---------------------------------------------------------------------------
_GENERIC_TITLES: set[str] = {
    "full-time",
    "full time",
    "fulltime",
    "part-time",
    "part time",
    "parttime",
    "remote",
    "onsite",
    "on-site",
    "hybrid",
    "contract",
    "contractor",
    "intern",
    "internship",
    "hiring",
    "we're hiring",
    "apply now",
    "multiple positions",
    "various roles",
    "see description",
    "n/a",
    "tbd",
}

# Patterns to strip from the END of titles (location / work-type suffixes)
_TITLE_TRAILING_RE = re.compile(
    r"\s*[|]\s*(?:remote|onsite|on-site|hybrid|full-time|part-time|"
    r"contract|(?:NYC|SF|LA|DC|US|EU|UK|APAC|EMEA|Global)|"
    r"[A-Z][a-z]+\s*,\s*[A-Z]{2})\s*$",
    re.IGNORECASE,
)

# Parenthetical location/work-type at end: "Engineer (Remote)" or "Engineer (NYC, NY)"
# Only matches known work-type keywords or City, ST patterns — NOT arbitrary words
# like "(Security)" or "(Spark)" which are team/specialization info
_TITLE_PAREN_SUFFIX_RE = re.compile(
    r"\s*\(\s*(?:remote|onsite|on-site|hybrid|full-time|part-time|contract|"
    r"(?:NYC|SF|LA|DC|US|EU|UK|APAC|EMEA|Global)|"
    r"[A-Z][a-z]+\s*,\s*[A-Z]{2})\s*\)\s*$",
    re.IGNORECASE,
)

# "HIRING: Frontend Developer" → "Frontend Developer"
_HIRING_PREFIX_RE = re.compile(
    r"^(?:HIRING|NOW HIRING|WE'RE HIRING|WANTED|SEEKING)[:\s-]+",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Company name cleaning patterns
# ---------------------------------------------------------------------------

# Parenthetical URLs: "Courted (https://courted.io)"
_COMPANY_PAREN_URL_RE = re.compile(r"\s*\(https?://[^)]*\)")

# YC batch: "PermitFlow (YC W22)"
_COMPANY_YC_RE = re.compile(r"\s*\(YC\s+[A-Z]\d{2,3}\)", re.IGNORECASE)

# Fundraising rounds: "(Series A)", "(Seed)", "(Series B)", etc.
_COMPANY_FUNDING_RE = re.compile(
    r"\s*\(\s*(?:Series\s+[A-Z]|Seed|Pre-Seed|Funded|Angel)\s*\)",
    re.IGNORECASE,
)

# "aka" parentheticals: "Careforce AI (aka Helpcare)"
_COMPANY_AKA_RE = re.compile(r"\s*\(\s*(?:aka|formerly|fka|prev\.?)\s+[^)]+\)", re.IGNORECASE)

# Trailing bare URLs: "Tether - https://tether.to"
_COMPANY_TRAILING_URL_RE = re.compile(r"\s*[-–—]\s*https?://\S+")
_COMPANY_BARE_URL_RE = re.compile(r"\s*https?://\S+")

# Domain suffixes: ".io", ".ai", ".com", etc.
_COMPANY_DOMAIN_SUFFIX_RE = re.compile(
    r"\.(io|ai|com|co|dev|tech|org|net|app|xyz|gg|so)$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public cleaning functions
# ---------------------------------------------------------------------------

def clean_title(title: str, source: str = "") -> tuple[str | None, list[str]]:
    """Clean a job title. Returns (cleaned_title or None, list of rules applied).

    Returns None as cleaned_title if no cleaning was needed.
    """
    original = title.strip()
    cleaned = original
    rules: list[str] = []

    # 0. Check for generic / junk titles
    if cleaned.lower().strip() in _GENERIC_TITLES:
        return None, ["generic_title_flagged"]

    # 1. Strip "HIRING:" prefixes
    m = _HIRING_PREFIX_RE.match(cleaned)
    if m:
        cleaned = cleaned[m.end():].strip()
        rules.append("strip_hiring_prefix")

    # 2. Strip trailing pipe-separated location/work-type
    prev = cleaned
    cleaned = _TITLE_TRAILING_RE.sub("", cleaned)
    if cleaned != prev:
        rules.append("strip_trailing_pipe_suffix")
    # Apply repeatedly for multiple trailing segments
    while True:
        prev2 = cleaned
        cleaned = _TITLE_TRAILING_RE.sub("", cleaned)
        if cleaned == prev2:
            break

    # 3. Strip parenthetical location/work-type
    prev = cleaned
    cleaned = _TITLE_PAREN_SUFFIX_RE.sub("", cleaned)
    if cleaned != prev:
        rules.append("strip_paren_suffix")

    # 4. ALL CAPS → Title Case (only if EVERY word is uppercase)
    words = cleaned.split()
    if len(words) >= 2 and all(w.isupper() and len(w) > 1 for w in words):
        cleaned = cleaned.title()
        rules.append("all_caps_to_title_case")

    # 5. Collapse whitespace, strip punctuation
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"^[,;:\-–—|]+|[,;:\-–—|]+$", "", cleaned).strip()
    if cleaned != original:
        if "whitespace_cleanup" not in rules:
            rules.append("whitespace_cleanup")

    if not rules or cleaned == original:
        return None, []

    return cleaned, rules


def clean_company_name(name: str) -> tuple[str | None, list[str]]:
    """Clean a company name. Returns (cleaned_name or None, list of rules applied).

    Returns None as cleaned_name if no cleaning was needed.
    """
    original = name.strip()
    cleaned = original
    rules: list[str] = []

    # 1. Strip parenthetical URLs
    prev = cleaned
    cleaned = _COMPANY_PAREN_URL_RE.sub("", cleaned)
    if cleaned != prev:
        rules.append("strip_paren_url")

    # 2. Strip YC batch tags
    prev = cleaned
    cleaned = _COMPANY_YC_RE.sub("", cleaned)
    if cleaned != prev:
        rules.append("strip_yc_batch")

    # 3. Strip fundraising rounds
    prev = cleaned
    cleaned = _COMPANY_FUNDING_RE.sub("", cleaned)
    if cleaned != prev:
        rules.append("strip_funding_round")

    # 4. Strip "aka" parentheticals
    prev = cleaned
    cleaned = _COMPANY_AKA_RE.sub("", cleaned)
    if cleaned != prev:
        rules.append("strip_aka")

    # 5. Strip trailing bare URLs
    prev = cleaned
    cleaned = _COMPANY_TRAILING_URL_RE.sub("", cleaned)
    cleaned = _COMPANY_BARE_URL_RE.sub("", cleaned)
    if cleaned != prev:
        rules.append("strip_trailing_url")

    # 6. Strip domain suffixes: .io, .ai, .com, etc.
    prev = cleaned
    cleaned = _COMPANY_DOMAIN_SUFFIX_RE.sub("", cleaned)
    if cleaned != prev:
        rules.append("strip_domain_suffix")

    # 7. Collapse whitespace, strip trailing punctuation
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"^[,;:\-–—|]+|[,;:\-–—|]+$", "", cleaned).strip()

    if not rules or cleaned == original:
        return None, []

    return cleaned, rules


def _looks_dirty_title(title: str) -> bool:
    """Heuristic: does this title need cleaning?"""
    t = title.strip()
    if t.lower() in _GENERIC_TITLES:
        return True
    if _HIRING_PREFIX_RE.match(t):
        return True
    if _TITLE_TRAILING_RE.search(t):
        return True
    if _TITLE_PAREN_SUFFIX_RE.search(t):
        return True
    words = t.split()
    if len(words) >= 2 and all(w.isupper() and len(w) > 1 for w in words):
        return True
    return False


def _looks_dirty_company(name: str) -> bool:
    """Heuristic: does this company name need cleaning?"""
    n = name.strip()
    if _COMPANY_PAREN_URL_RE.search(n):
        return True
    if _COMPANY_YC_RE.search(n):
        return True
    if _COMPANY_FUNDING_RE.search(n):
        return True
    if _COMPANY_AKA_RE.search(n):
        return True
    if _COMPANY_TRAILING_URL_RE.search(n):
        return True
    if _COMPANY_BARE_URL_RE.search(n):
        return True
    if _COMPANY_DOMAIN_SUFFIX_RE.search(n):
        return True
    return False


async def clean_jobs_batch(session: AsyncSession) -> dict:
    """Apply deterministic cleaning to all jobs. Idempotent.

    Only processes jobs where clean_* IS NULL and the raw value looks dirty.
    Returns counts of what was cleaned and what was flagged for LLM.
    """
    stats = {"titles_cleaned": 0, "companies_cleaned": 0, "flagged_for_llm": 0, "skipped": 0}

    result = await session.execute(select(Job))
    jobs = list(result.scalars().all())

    for job in jobs:
        # --- Title cleaning ---
        if job.clean_title is None and _looks_dirty_title(job.title):
            cleaned, rules = clean_title(job.title, job.source.value if job.source else "")
            if cleaned is not None:
                job.clean_title = cleaned
                meta = dict(job.extra_metadata or {})
                meta["title_cleaning_rules"] = rules
                job.extra_metadata = meta
                stats["titles_cleaned"] += 1
            elif "generic_title_flagged" in rules:
                # Flag for LLM (Layer 3)
                meta = dict(job.extra_metadata or {})
                meta["needs_llm_cleaning"] = True
                meta["llm_cleaning_reason"] = "generic_title"
                job.extra_metadata = meta
                stats["flagged_for_llm"] += 1

        # --- Company name cleaning ---
        if job.clean_company is None and _looks_dirty_company(job.company):
            cleaned, rules = clean_company_name(job.company)
            if cleaned is not None:
                job.clean_company = cleaned
                meta = dict(job.extra_metadata or {})
                meta["company_cleaning_rules"] = rules
                job.extra_metadata = meta
                stats["companies_cleaned"] += 1

    # Update pipeline_stage for cleaned jobs
    now = datetime.now(timezone.utc)
    for job in jobs:
        if job.pipeline_stage == "scraped" and (job.clean_title is not None or job.clean_company is not None):
            job.pipeline_stage = "cleaned"
            job.cleaned_at = now

    await session.commit()
    logger.info(
        "Batch cleaning: %d titles, %d companies cleaned, %d flagged for LLM",
        stats["titles_cleaned"],
        stats["companies_cleaned"],
        stats["flagged_for_llm"],
    )
    return stats
