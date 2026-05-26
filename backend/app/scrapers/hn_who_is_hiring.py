"""Hacker News 'Who is hiring?' thread scraper.

Uses the Algolia HN Search API (public, no auth):
  - Find latest monthly thread: search_by_date?query="Ask HN: Who is hiring"&tags=ask_hn
  - Fetch all top-level comments: search?tags=comment,story_{id}

Each top-level comment is a job posting. Format varies but commonly:
  Company | Role | Location | Remote | ...
  Description...
  URL
"""

import logging
import re
from datetime import datetime

import httpx

from app.scrapers.base import ScrapedJob, html_to_text

logger = logging.getLogger(__name__)

ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
ALGOLIA_ITEMS = "https://hn.algolia.com/api/v1/search"
HN_ITEM_URL = "https://news.ycombinator.com/item?id={}"

# Default keywords for filtering HN posts (research/AI focused)
DEFAULT_INCLUDE = [
    "research",
    "scientist",
    "machine learning",
    "ML",
    "AI",
    "data science",
    "NLP",
    "policy",
    "technical staff",
    "deep learning",
    "LLM",
    "GPT",
    "safety",
    "alignment",
]

DEFAULT_EXCLUDE = [
    "blockchain",
    "crypto",
    "web3",
    "nft",
]


def _clean_hn_company(raw: str) -> str:
    """Strip URLs, YC batch tags, domain suffixes from HN company names."""
    name = raw.strip()

    # 1. Strip parenthetical URLs: "Courted (https://...)" → "Courted"
    name = re.sub(r"\s*\(https?://[^)]*\)", "", name)

    # 2. Strip YC batch: "PermitFlow (YC W22)" → "PermitFlow"
    name = re.sub(r"\s*\(YC\s+[A-Z]\d{2,3}\)", "", name, flags=re.IGNORECASE)

    # 3. Strip fundraising: "(Series A)", "(Seed)" → ""
    name = re.sub(
        r"\s*\(\s*(?:Series\s+[A-Z]|Seed|Pre-Seed|Funded|Angel)\s*\)",
        "",
        name,
        flags=re.IGNORECASE,
    )

    # 4. Strip trailing URLs: "Tether - https://..." → "Tether"
    name = re.sub(r"\s*[-–—]\s*https?://\S+", "", name)
    name = re.sub(r"\s*https?://\S+", "", name)

    # 5. Strip domain suffixes: .io, .ai, .com, .co, .dev, .tech
    name = re.sub(r"\.(io|ai|com|co|dev|tech|org|net)$", "", name, flags=re.IGNORECASE)

    return name.strip()


def _clean_hn_title(raw: str, description: str) -> str:
    """Validate title; extract from description if generic."""
    generic = {
        "full-time",
        "full time",
        "fulltime",
        "part-time",
        "part time",
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
        "multiple positions",
    }
    title = raw.strip()

    # Strip "HIRING:" prefixes
    title = re.sub(
        r"^(?:HIRING|NOW HIRING|WE'RE HIRING|WANTED|SEEKING)[:\s-]+", "", title, flags=re.IGNORECASE
    ).strip()

    if title.lower() not in generic:
        return title

    # Try to extract real title from description
    snippet = description[:300]

    # Common patterns: "seeking a [Title]", "Role: [Title]", "looking for [a/an] [Title]"
    patterns = [
        r"(?:seeking|hiring|looking for)\s+(?:a|an)\s+([A-Z][A-Za-z /&-]+)",
        r"(?:role|position|title)\s*:\s*([A-Z][A-Za-z /&-]+)",
        r"^([A-Z][A-Za-z]+(?: [A-Z][a-z]+){1,4})\s*[-–—]",
    ]
    for pat in patterns:
        m = re.search(pat, snippet)
        if m:
            extracted = m.group(1).strip()
            if len(extracted) > 5 and extracted.lower() not in generic:
                return extracted

    return title


async def scrape_hn_who_is_hiring(
    http_client: httpx.AsyncClient,
    *,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    max_threads: int = 1,
) -> list[ScrapedJob]:
    """Scrape the latest HN 'Who is hiring?' thread(s).

    Args:
        include_patterns: Keywords to match (defaults to AI/research terms).
        exclude_patterns: Keywords to reject.
        max_threads: Number of recent threads to scrape (default: latest only).

    Returns:
        List of ScrapedJob from matching comments.
    """
    include = include_patterns or DEFAULT_INCLUDE
    exclude = exclude_patterns or DEFAULT_EXCLUDE

    # Step 1: find latest thread(s)
    threads = await _find_threads(http_client, max_threads)
    if not threads:
        logger.warning("No 'Who is hiring?' threads found")
        return []

    all_jobs: list[ScrapedJob] = []
    for thread in threads:
        thread_id = thread["objectID"]
        thread_title = thread.get("title", "")
        logger.info("Scraping HN thread: %s (ID: %s)", thread_title, thread_id)

        # Step 2: fetch all top-level comments
        comments = await _fetch_comments(http_client, thread_id)
        logger.info("Found %d comments in thread %s", len(comments), thread_id)

        # Step 3: parse each comment into a job posting
        for comment in comments:
            job = _parse_comment(comment, thread_title)
            if job and _passes_filter(job, include, exclude):
                all_jobs.append(job)

    logger.info("Parsed %d relevant jobs from HN threads", len(all_jobs))
    return all_jobs


async def _find_threads(http_client: httpx.AsyncClient, limit: int) -> list[dict]:
    """Find recent 'Who is hiring?' threads via Algolia."""
    resp = await http_client.get(
        ALGOLIA_SEARCH,
        params={
            "query": '"Ask HN: Who is hiring"',
            "tags": "ask_hn",
            "hitsPerPage": limit,
        },
    )
    resp.raise_for_status()
    return resp.json().get("hits", [])


async def _fetch_comments(http_client: httpx.AsyncClient, thread_id: str) -> list[dict]:
    """Fetch all top-level comments from a thread via Algolia."""
    all_comments: list[dict] = []
    page = 0

    while True:
        resp = await http_client.get(
            ALGOLIA_ITEMS,
            params={
                "tags": f"comment,story_{thread_id}",
                "hitsPerPage": 500,
                "page": page,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits", [])

        # Only keep top-level comments (parent is the thread itself)
        top_level = [h for h in hits if str(h.get("parent_id")) == str(thread_id)]
        all_comments.extend(top_level)

        if page >= data.get("nbPages", 1) - 1:
            break
        page += 1

    return all_comments


def _parse_comment(comment: dict, thread_title: str) -> ScrapedJob | None:
    """Parse a single HN comment into a ScrapedJob.

    Returns None if the comment doesn't look like a job posting.
    """
    raw_html = comment.get("comment_text", "")
    if not raw_html or len(raw_html) < 50:
        return None

    plain = html_to_text(raw_html)
    lines = plain.strip().split("\n")
    if not lines:
        return None

    # Parse the header line (usually pipe-separated)
    header = lines[0]
    parts = [p.strip() for p in header.split("|")]

    # Need at least company + something
    if len(parts) < 2:
        # Try colon-based format: "Company: Role - Location"
        if ":" in header:
            parts = [p.strip() for p in header.split(":", 1)]
        else:
            return None

    raw_company = parts[0].strip()
    if not raw_company or len(raw_company) > 100:
        return None

    # Clean company name at parse time (Layer 1)
    company = _clean_hn_company(raw_company)

    # Second part is usually the role
    raw_title = parts[1].strip() if len(parts) > 1 else ""
    if not raw_title:
        return None

    # Description is everything after the header (needed for title cleaning)
    description = "\n".join(lines[1:]).strip()

    # Clean title at parse time (Layer 1)
    title = _clean_hn_title(raw_title, description)

    # Remaining parts: try to identify location, remote, etc.
    location = None
    remote = False
    for part in parts[2:]:
        lower = part.lower().strip()
        if "remote" in lower:
            remote = True
            # Could also contain location info like "Remote (US)"
            if len(lower) > 7:
                location = part.strip()
        elif not location and _looks_like_location(lower):
            location = part.strip()

    # Also check header for "remote" keyword
    if "remote" in header.lower():
        remote = True

    # Extract URLs from the full text
    urls = re.findall(r"https?://[^\s<>\"']+", plain)
    # Strip trailing punctuation that gets glued onto URLs by HN's
    # comment formatting ("apply at example.com)." → "example.com).").
    # Without this, ~10-20% of harvested URLs hit DNS errors downstream.
    urls = [re.sub(r"[.,;:!?\)\]\}>]+$", "", u) for u in urls]
    careers_url = None
    for u in urls:
        # Prefer career/job URLs
        if any(
            kw in u.lower()
            for kw in ["career", "jobs", "hiring", "apply", "lever", "greenhouse", "ashby"]
        ):
            careers_url = u
            break
    if not careers_url and urls:
        careers_url = urls[0]

    # Use HN comment permalink as the canonical URL
    comment_id = comment.get("objectID", "")
    url = HN_ITEM_URL.format(comment_id)

    # Parse salary from description
    from app.scrapers.base import parse_salary

    salary_min, salary_max = parse_salary(plain)

    # Posted date from comment timestamp
    posted_at = None
    created = comment.get("created_at")
    if created:
        try:
            posted_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

    return ScrapedJob(
        title=title,
        company_name=company,
        url=url,
        description=description if description else plain,
        description_html=raw_html,
        location=location,
        remote=remote,
        salary_min=salary_min,
        salary_max=salary_max,
        posted_at=posted_at,
        application_url=careers_url or url,
        source="hn_who_is_hiring",
        metadata={
            "hn_comment_id": comment_id,
            "hn_thread_title": thread_title,
            "careers_url": careers_url,
            "raw_header": header,
        },
    )


def _passes_filter(job: ScrapedJob, include: list[str], exclude: list[str]) -> bool:
    """Check if a parsed HN job matches keyword filters."""
    search_text = f"{job.title} {job.description[:500]}".lower()

    if any(pat.lower() in search_text for pat in exclude):
        return False

    if include and not any(pat.lower() in search_text for pat in include):
        return False

    return True


def _looks_like_location(text: str) -> bool:
    """Heuristic: does this text fragment look like a location?"""
    location_hints = [
        ",",
        "san",
        "new york",
        "nyc",
        "london",
        "berlin",
        "paris",
        "seattle",
        "austin",
        "boston",
        "chicago",
        "dc",
        "denver",
        "toronto",
        "vancouver",
        "sydney",
        "singapore",
        "tokyo",
        "usa",
        "uk",
        "eu",
        "europe",
        "us ",
        "ca ",
        "bay area",
    ]
    return any(hint in text for hint in location_hints)
