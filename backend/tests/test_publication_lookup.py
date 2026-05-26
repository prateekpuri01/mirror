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
    """enrich_publication returns a complete ProfilePublication dict."""
    paper = {
        "title": "Test Paper",
        "authors": ["Jane Doe", "Jane Doe"],
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
        "skills": {"technical": ["Python", "machine learning"], "communication": [], "tools": []},
        "target_roles": [{"title": "Data Scientist"}],
        "domains": ["AI"],
        "work_history": [{"key": "acme_ai", "employer": "Acme AI", "start": "2022", "end": ""}],
    }

    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"impact_summary":"Led testing","so_what":"Shows testing skills","skills_demonstrated":["Python"],"quantitative_specifics":["10 citations"],"relevance_weight":0.8,"work_history_key":"rand_corporation","type":"journal"}'
            )
        )
    ]

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch("app.ai.publication_enricher.get_openai_client", return_value=mock_client):
        result = await enrich_publication(paper, profile)

    assert result["auto_populated"] is True
    assert result["title"] == "Test Paper"
    assert result["first_author"] is True
    assert result["impact_summary"] == "Led testing"
    assert result["work_history_key"] == "rand_corporation"
    assert "Python" in result["skills_demonstrated"]
