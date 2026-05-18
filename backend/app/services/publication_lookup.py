"""Semantic Scholar API client for publication lookup and bulk import."""

import logging
from difflib import SequenceMatcher

import httpx

logger = logging.getLogger(__name__)

SEMANTIC_SCHOLAR_BASE = "https://api.semanticscholar.org/graph/v1"
PAPER_FIELDS = "title,authors,venue,year,abstract,externalIds,publicationTypes,citationCount"
AUTHOR_PAPER_FIELDS = "title,authors,venue,year,abstract,externalIds,citationCount,publicationTypes"


def _title_similarity(a: str, b: str) -> float:
    """Fuzzy title similarity using SequenceMatcher."""
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


async def search_publication(title: str) -> dict | None:
    """Search Semantic Scholar for a publication by title.

    Returns the best matching paper as a normalized dict, or None if no good match.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{SEMANTIC_SCHOLAR_BASE}/paper/search",
            params={"query": title, "limit": 3, "fields": PAPER_FIELDS},
        )
        if resp.status_code != 200:
            logger.warning("Semantic Scholar search failed: %s %s", resp.status_code, resp.text[:200])
            return None

        data = resp.json()
        papers = data.get("data", [])
        if not papers:
            return None

    # Pick the best match by title similarity
    best = max(papers, key=lambda p: _title_similarity(title, p.get("title", "")))
    similarity = _title_similarity(title, best.get("title", ""))
    if similarity < 0.5:
        logger.info("Best match similarity %.2f too low for '%s'", similarity, title)
        return None

    return _normalize_paper(best)


async def fetch_author_publications(scholar_url: str | None = None, author_name: str | None = None) -> list[dict]:
    """Fetch all publications for an author via Semantic Scholar.

    Can search by Google Scholar URL (extracting name) or directly by name.
    Returns a list of normalized paper dicts.
    """
    if not author_name and not scholar_url:
        return []

    search_name = author_name or ""

    async with httpx.AsyncClient(timeout=20.0) as client:
        # Search for author by name
        resp = await client.get(
            f"{SEMANTIC_SCHOLAR_BASE}/author/search",
            params={"query": search_name, "limit": 3},
        )
        if resp.status_code != 200:
            logger.warning("Author search failed: %s", resp.status_code)
            return []

        authors = resp.json().get("data", [])
        if not authors:
            return []

        # Use the first matching author
        author_id = authors[0].get("authorId")
        if not author_id:
            return []

        # Fetch their papers
        resp = await client.get(
            f"{SEMANTIC_SCHOLAR_BASE}/author/{author_id}/papers",
            params={"limit": 100, "fields": AUTHOR_PAPER_FIELDS},
        )
        if resp.status_code != 200:
            logger.warning("Author papers fetch failed: %s", resp.status_code)
            return []

        papers = resp.json().get("data", [])

    return [_normalize_paper(p) for p in papers]


def _normalize_paper(paper: dict) -> dict:
    """Normalize a Semantic Scholar paper dict to our standard format."""
    external_ids = paper.get("externalIds") or {}
    doi = external_ids.get("DOI")
    arxiv_id = external_ids.get("ArXiv")

    url = None
    if doi:
        url = f"https://doi.org/{doi}"
    elif arxiv_id:
        url = f"https://arxiv.org/abs/{arxiv_id}"

    pub_types = paper.get("publicationTypes") or []
    pub_type = "journal"
    if "Conference" in pub_types:
        pub_type = "conference"
    elif "Review" in pub_types:
        pub_type = "report"

    authors = [a.get("name", "") for a in (paper.get("authors") or [])]

    result = {
        "title": paper.get("title", ""),
        "authors": authors,
        "venue": paper.get("venue", ""),
        "year": str(paper.get("year", "")) if paper.get("year") else "",
        "abstract": paper.get("abstract") or "",
        "doi": doi,
        "arxiv_id": arxiv_id,
        "url": url,
        "type": pub_type,
        "citation_count": paper.get("citationCount"),
    }
    return result


async def fetch_arxiv_full_text(arxiv_id: str) -> str | None:
    """Download an arXiv PDF and extract full text.

    Returns the extracted text or None if fetching/parsing fails.
    """
    if not arxiv_id:
        return None

    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    logger.info("Fetching arXiv full text: %s", pdf_url)

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(pdf_url)
            if resp.status_code != 200:
                logger.warning("ArXiv PDF fetch failed: %s for %s", resp.status_code, arxiv_id)
                return None

        import fitz  # pymupdf
        doc = fitz.open(stream=resp.content, filetype="pdf")
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()

        full_text = "\n".join(text_parts).strip()
        if len(full_text) < 100:
            logger.warning("ArXiv PDF text too short (%d chars) for %s", len(full_text), arxiv_id)
            return None

        # Cap at ~15K chars to avoid bloating the profile JSONB
        if len(full_text) > 15000:
            full_text = full_text[:15000]

        logger.info("Extracted %d chars from arXiv PDF %s", len(full_text), arxiv_id)
        return full_text

    except Exception:
        logger.warning("Failed to fetch/parse arXiv PDF for %s", arxiv_id, exc_info=True)
        return None


async def enrich_publication_with_full_text(pub: dict) -> dict:
    """If the publication has an arXiv ID, fetch and store the full text."""
    arxiv_id = pub.get("arxiv_id")
    if not arxiv_id:
        # Try to extract from URL
        url = pub.get("url") or ""
        if "arxiv.org/abs/" in url:
            arxiv_id = url.split("arxiv.org/abs/")[-1].split("?")[0].strip("/")

    if not arxiv_id:
        return pub

    if pub.get("full_text"):
        # Already has full text
        return pub

    full_text = await fetch_arxiv_full_text(arxiv_id)
    if full_text:
        pub["full_text"] = full_text
        pub["arxiv_id"] = arxiv_id

    return pub
