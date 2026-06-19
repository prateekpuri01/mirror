"""Tests for publication lookup and enrichment."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.publication_enricher import enrich_publication
from app.services.publication_lookup import _title_similarity, search_publication


def test_title_similarity():
    assert _title_similarity("Hello World", "hello world") == 1.0
    assert _title_similarity("Hello World", "Hello") < 1.0
    assert _title_similarity("", "") == 1.0


@pytest.mark.asyncio
async def test_search_publication_picks_best_match():
    """search_publication picks the paper with highest title similarity."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [
            {
                "title": "Some unrelated paper",
                "authors": [{"name": "Author A"}],
                "venue": "Journal X",
                "year": 2024,
                "abstract": "Abstract 1",
                "externalIds": {"DOI": "10.1234/a"},
                "publicationTypes": ["JournalArticle"],
                "citationCount": 5,
            },
            {
                "title": "Small models big threats characterizing safety challenges",
                "authors": [{"name": "Jane Doe"}],
                "venue": "AAAI 2026",
                "year": 2026,
                "abstract": "Examines how smaller AI systems...",
                "externalIds": {"ArXiv": "2601.21365"},
                "publicationTypes": ["Conference"],
                "citationCount": 0,
            },
        ]
    }

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.publication_lookup.httpx.AsyncClient", return_value=mock_client):
        result = await search_publication("Small models, big threats")

    assert result is not None
    assert "Small models" in result["title"]
    assert result["url"] == "https://arxiv.org/abs/2601.21365"
    assert result["type"] == "conference"


@pytest.mark.asyncio
async def test_search_publication_returns_none_on_low_similarity():
    """search_publication returns None if no paper has good title match."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [
            {
                "title": "Completely unrelated paper about quantum physics",
                "authors": [],
                "venue": "",
                "year": 2020,
                "abstract": "",
                "externalIds": {},
                "publicationTypes": [],
                "citationCount": 0,
            }
        ]
    }

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.publication_lookup.httpx.AsyncClient", return_value=mock_client):
        result = await search_publication("AI Security Guide and Risk Assessment Tool")

    assert result is None


@pytest.mark.asyncio
async def test_search_publication_returns_none_on_empty():
    """search_publication returns None when no papers found."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": []}

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.publication_lookup.httpx.AsyncClient", return_value=mock_client):
        result = await search_publication("Nonexistent paper title")

    assert result is None


@pytest.mark.asyncio
async def test_enrich_publication_returns_complete_dict():
    """enrich_publication returns a complete ProfilePublication dict.

    The enricher is now deterministic — no LLM call. Verifies:
      * first_author detected via authors[0] substring match
      * work_history_key matched by paper year against work_history ranges
      * relevance_weight is a sensible float in (0, 1]
      * dropped narrative fields are empty placeholders (back-compat)
      * abstract passes through from the source
    """
    paper = {
        "title": "Test Paper",
        "authors": ["Jane Doe", "Bob Smith"],
        "venue": "Test Venue",
        "year": "2025",
        "abstract": "This paper tests things.",
        "doi": "10.1234/test",
        "url": "https://doi.org/10.1234/test",
        "type": "journal",
        "citation_count": 10,
    }

    profile = {
        "personal": {"name": "Jane Doe"},
        "work_history": [
            {"key": "acme_ai", "employer": "Acme AI", "start": "2022", "end": "present"},
            {"key": "old_lab", "employer": "Old Lab", "start": "2018", "end": "2021"},
        ],
    }

    result = await enrich_publication(paper, profile)

    # Pass-through from the source paper
    assert result["title"] == "Test Paper"
    assert result["abstract"] == "This paper tests things."
    assert result["doi"] == "10.1234/test"
    assert result["citation_count"] == 10
    assert result["auto_populated"] is True

    # Deterministic computations
    assert result["first_author"] is True  # "jane doe" in authors[0]
    assert result["work_history_key"] == "acme_ai"  # 2025 falls in 2022-present
    assert 0.0 < result["relevance_weight"] <= 1.0

    # Dropped narrative fields remain as empty placeholders for back-compat
    assert result["impact_summary"] == ""
    assert result["so_what"] == ""
    assert result["skills_demonstrated"] == []
    assert result["quantitative_specifics"] == []


@pytest.mark.asyncio
async def test_enrich_publication_no_first_author_match():
    """When the user name isn't authors[0], first_author should be False."""
    paper = {
        "title": "Other Paper",
        "authors": ["Bob Smith", "Jane Doe"],
        "year": "2024",
        "citation_count": 0,
    }
    profile = {"personal": {"name": "Jane Doe"}, "work_history": []}
    result = await enrich_publication(paper, profile)
    assert result["first_author"] is False
    assert result["work_history_key"] is None


@pytest.mark.asyncio
async def test_relevance_weight_orders_papers_correctly():
    """Recent first-author papers should outrank old non-first papers."""
    recent_fa = await enrich_publication(
        {"title": "A", "authors": ["Jane Doe"], "year": "2025", "citation_count": 5},
        {"personal": {"name": "Jane Doe"}},
    )
    old_non_fa = await enrich_publication(
        {"title": "B", "authors": ["Bob", "Jane Doe"], "year": "2010", "citation_count": 5},
        {"personal": {"name": "Jane Doe"}},
    )
    assert recent_fa["relevance_weight"] > old_non_fa["relevance_weight"]
