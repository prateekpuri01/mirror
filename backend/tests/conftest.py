"""Shared fixtures for search preferences tests."""

import pytest


@pytest.fixture
def sample_profile_new():
    """Profile using new free-text search preference fields."""
    return {
        "target_roles": [
            {"title": "Research Scientist", "seniority": "Senior"},
            {"title": "Data Scientist", "seniority": "Mid"},
        ],
        "domains": ["computational social science", "AI safety"],
        "skills": {"technical": ["python", "pytorch", "nlp", "statistics"]},
        "search_preferences": {
            "looking_for": "Research-heavy roles at mission-driven orgs working on AI safety or computational social science. Prefer academic-adjacent culture with publishing encouraged.",
            "not_looking_for": "Defense contractors, surveillance tech, adtech, companies with no remote flexibility. No pure frontend or DevOps roles.",
            "positive_signals": [
                "ai safety",
                "computational social science",
                "research culture",
                "mission-driven",
                "publishing encouraged",
            ],
            "exclusions": [
                "defense contractor",
                "surveillance",
                "adtech",
                "no remote",
            ],
        },
    }


@pytest.fixture
def sample_profile_legacy():
    """Profile using legacy structured search preference fields."""
    return {
        "target_roles": [
            {"title": "Research Scientist", "seniority": "Senior"},
            {"title": "Data Scientist", "seniority": "Mid"},
        ],
        "domains": ["computational social science", "AI safety"],
        "skills": {"technical": ["python", "pytorch", "nlp", "statistics"]},
        "search_preferences": {
            "industries_ranked": [
                "Academic research labs",
                "AI safety organizations",
                "Public interest technology",
            ],
            "nice_to_haves": [
                "Publishing culture",
                "Research freedom",
            ],
            "deal_breakers": [
                "Defense contractors (Raytheon, Lockheed)",
                "Surveillance technology",
            ],
            "org_anti_patterns": [
                "No remote flexibility",
                "Micromanagement culture",
            ],
        },
    }
