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
        self, company: Company, http_client: httpx.AsyncClient
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

_SALARY_PATTERN = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(?:[-–—]|to)\s*\$?\s*([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)


def parse_salary(text: str) -> tuple[int | None, int | None]:
    """Extract a salary range from text. Returns (min, max) or (None, None)."""
    match = _SALARY_PATTERN.search(text)
    if match:
        lo = int(float(match.group(1).replace(",", "")))
        hi = int(float(match.group(2).replace(",", "")))
        # Sanity check: ignore tiny numbers that are probably not salaries
        if lo >= 20_000 and hi >= lo:
            return lo, hi
    return None, None
