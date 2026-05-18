"""Tests for LinkedIn scraping and accomplishment generation."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.linkedin_enricher import generate_accomplishments_from_linkedin
from app.services.linkedin_scraper import scrape_linkedin_profile


@pytest.mark.asyncio
async def test_scrape_linkedin_invalid_url():
    """scrape_linkedin_profile returns error for invalid URL."""
    result = await scrape_linkedin_profile("https://example.com/not-linkedin")
    assert result["success"] is False
    assert "Invalid" in result["error"]


@pytest.mark.asyncio
async def test_scrape_linkedin_empty_url():
    """scrape_linkedin_profile returns error for empty URL."""
    result = await scrape_linkedin_profile("")
    assert result["success"] is False


@pytest.mark.asyncio
async def test_scrape_linkedin_extracts_text():
    """scrape_linkedin_profile extracts text from page."""
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_timeout = AsyncMock()
    mock_page.url = "https://linkedin.com/in/jane-doe"
    mock_page.inner_text = AsyncMock(
        return_value="Jane Doe\nML Research Scientist at Acme AI\nExperience\nAcme AI\nResearch Scientist\n2022 - Present\nBuilt evaluation framework..."
    )

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_chromium = AsyncMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw = AsyncMock()
    mock_pw.chromium = mock_chromium
    mock_pw.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.linkedin_scraper.async_playwright", return_value=mock_pw):
        result = await scrape_linkedin_profile("https://linkedin.com/in/jane-doe")

    assert result["success"] is True
    assert "Acme AI" in result["raw_text"]
    assert result["scraped_at"] is not None


@pytest.mark.asyncio
async def test_scrape_linkedin_detects_auth_wall():
    """scrape_linkedin_profile detects auth wall via URL redirect."""
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_timeout = AsyncMock()
    mock_page.url = "https://www.linkedin.com/authwall?..."
    mock_page.inner_text = AsyncMock(return_value="Sign in to view profile")

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_chromium = AsyncMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw = AsyncMock()
    mock_pw.chromium = mock_chromium
    mock_pw.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.linkedin_scraper.async_playwright", return_value=mock_pw):
        result = await scrape_linkedin_profile("https://linkedin.com/in/jane-doe")

    assert result["success"] is False
    assert "authentication" in result["error"].lower()


@pytest.mark.asyncio
async def test_generate_accomplishments_returns_tagged_list():
    """generate_accomplishments_from_linkedin returns accomplishments with auto_populated and source."""
    linkedin_text = """
    Jane Doe
    ML Research Scientist at Acme AI
    Experience
    Acme AI - Research Scientist (2022-present)
    - Built LLM evaluation framework
    WidgetCo - Senior Data Scientist (2019-2022)
    - Developed fraud detection models
    """

    profile_data = {
        "skills": {"technical": ["Python", "machine learning"], "communication": [], "tools": []},
        "target_roles": [{"title": "Data Scientist"}],
        "domains": ["AI"],
        "work_history": [
            {"key": "acme_ai", "employer": "Acme AI", "title": "Research Scientist", "start": "2022", "end": ""},
            {"key": "widgetco", "employer": "WidgetCo", "title": "Senior Data Scientist", "start": "2019", "end": "2022"},
        ],
        "complete_profile": {
            "accomplishments": [
                {"employer": "Acme AI", "title": "Evaluation Framework", "date_range": "2023-2025"}
            ],
            "publications": [
                {"id": "pub-arxiv-eval", "title": "LLM Evaluation Benchmark", "year": "2025"}
            ],
        },
    }

    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='[{"id":"linkedin-widgetco-fraud","title":"Fraud Detection System","employer":"WidgetCo","work_history_key":"widgetco","date_range":"2019-2022","impact_summary":"Built ML models for fraud detection","quantitative_specifics":["10TB+ data processed"],"so_what":"Shows ML at scale","skills_demonstrated":["Python","machine learning"],"relevance_weight":0.7,"related_publication_ids":[]}]'
            )
        )
    ]

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch("app.ai.linkedin_enricher.get_openai_client", return_value=mock_client):
        result = await generate_accomplishments_from_linkedin(linkedin_text, profile_data)

    assert len(result) == 1
    assert result[0]["auto_populated"] is True
    assert result[0]["source"] == "linkedin"
    assert result[0]["work_history_key"] == "widgetco"
    assert result[0]["title"] == "Fraud Detection System"


@pytest.mark.asyncio
async def test_generate_accomplishments_handles_bad_json():
    """generate_accomplishments_from_linkedin handles malformed LLM response."""
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(content="This is not valid JSON"))
    ]

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch("app.ai.linkedin_enricher.get_openai_client", return_value=mock_client):
        result = await generate_accomplishments_from_linkedin("Some text", {"skills": {}, "work_history": []})

    assert result == []
