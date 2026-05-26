"""Shared types and utilities for all scrapers."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from typing import Protocol

import httpx

from app.models.companies import Company


@dataclass
class ScrapedJob:
    """Normalized intermediate format all scrapers produce."""

    title: str
    company_name: str
    url: str
    description: str
    description_html: str | None = None
    location: str | None = None
    remote: bool = False
    salary_min: int | None = None
    salary_max: int | None = None
    posted_at: datetime | None = None
    application_url: str | None = None
    source: str = "manual"
    metadata: dict = field(default_factory=dict)


class ScraperProtocol(Protocol):
    source_name: str

    def can_handle(self, company: Company) -> bool: ...

    async def scrape_company(
        self,
        company: Company,
        http_client: httpx.AsyncClient,
        *,
        known_urls: set[str] | None = None,
    ) -> list[ScrapedJob]: ...


# ---------------------------------------------------------------------------
# HTML → plain text (stdlib only, no BeautifulSoup)
# ---------------------------------------------------------------------------


class _HTMLToText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip = True
        elif tag in ("br", "p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"):
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = False
        elif tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)


def html_to_text(html: str) -> str:
    """Convert HTML to readable plain text."""
    parser = _HTMLToText()
    parser.feed(html)
    raw = "".join(parser._parts)
    # Collapse blank lines and strip each line
    lines = [line.strip() for line in raw.splitlines()]
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# Salary parsing
# ---------------------------------------------------------------------------

_SALARY_RANGE_PATTERN = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*([KkMm])?\s*(?:[-–—]|to)\s*\$?\s*([\d,]+(?:\.\d+)?)\s*([KkMm])?",
    re.IGNORECASE,
)

# Single salary value (e.g. "$295K", "$150,000") — used as fallback when no range found
_SALARY_SINGLE_PATTERN = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*([KkMm])?(?:\s|$|[^-–—\d])",
    re.IGNORECASE,
)

_SUFFIX_MULTIPLIER = {"k": 1_000, "m": 1_000_000}


def _apply_suffix(value: float, suffix: str) -> int:
    return int(value * _SUFFIX_MULTIPLIER.get(suffix.lower(), 1))


def parse_salary(text: str) -> tuple[int | None, int | None]:
    """Extract a salary range from text. Returns (min, max) or (None, None).

    Handles formats like:
    - Ranges: $150,000 – $250,000, $288K – $425K, $230.4K – $425K
    - Single values: $295K, $150,000 (returned as both min and max)
    """
    # Try range first
    match = _SALARY_RANGE_PATTERN.search(text)
    if match:
        lo = float(match.group(1).replace(",", ""))
        lo_suffix = (match.group(2) or "").lower()
        hi = float(match.group(3).replace(",", ""))
        hi_suffix = (match.group(4) or lo_suffix).lower()

        lo = _apply_suffix(lo, lo_suffix)
        hi = _apply_suffix(hi, hi_suffix)

        if lo >= 20_000 and hi >= lo:
            return lo, hi

    # Fall back to single value
    match = _SALARY_SINGLE_PATTERN.search(text)
    if match:
        val = float(match.group(1).replace(",", ""))
        suffix = (match.group(2) or "").lower()
        val = _apply_suffix(val, suffix)

        if val >= 20_000:
            return val, val

    return None, None
