"""Tests for keyword generation from free-text preferences (mocked LLM)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.keyword_generator import generate_preference_keywords


@pytest.fixture
def mock_openai_response():
    """Create a mock OpenAI chat completion response."""

    def _make(content: str):
        message = MagicMock()
        message.content = content
        choice = MagicMock()
        choice.message = message
        response = MagicMock()
        response.choices = [choice]
        return response

    return _make


class TestGeneratePreferenceKeywords:
    @pytest.mark.asyncio
    async def test_basic_extraction(self, mock_openai_response):
        canned = json.dumps(
            {
                "positive_signals": ["ai safety", "research culture", "mission-driven"],
                "exclusions": ["defense contractor", "surveillance"],
            }
        )
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_openai_response(canned))

        with patch("app.ai.keyword_generator.get_openai_client", return_value=mock_client):
            result = await generate_preference_keywords(
                "Research roles in AI safety",
                "No defense contractors",
            )

        assert result["positive_signals"] == ["ai safety", "research culture", "mission-driven"]
        assert result["exclusions"] == ["defense contractor", "surveillance"]

    @pytest.mark.asyncio
    async def test_markdown_fence_stripping(self, mock_openai_response):
        canned = (
            "```json\n"
            + json.dumps(
                {
                    "positive_signals": ["nlp", "transformers"],
                    "exclusions": ["adtech"],
                }
            )
            + "\n```"
        )
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_openai_response(canned))

        with patch("app.ai.keyword_generator.get_openai_client", return_value=mock_client):
            result = await generate_preference_keywords("NLP research", "No adtech")

        assert "nlp" in result["positive_signals"]
        assert "adtech" in result["exclusions"]

    @pytest.mark.asyncio
    async def test_empty_input_no_api_call(self):
        result = await generate_preference_keywords("", "")
        assert result == {"positive_signals": [], "exclusions": []}

    @pytest.mark.asyncio
    async def test_malformed_json_returns_empty(self, mock_openai_response):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=mock_openai_response("not valid json at all")
        )

        with patch("app.ai.keyword_generator.get_openai_client", return_value=mock_client):
            result = await generate_preference_keywords("Something", "Something else")

        assert result == {"positive_signals": [], "exclusions": []}
