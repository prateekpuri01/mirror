"""Seed script — run with: docker compose exec api python -m app.seed"""

import asyncio
import logging

from sqlalchemy import text

from app.database import async_session
from app.models import (
    Company,
    SearchProfile,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def seed():
    async with async_session() as session:
        # Clear existing data (FK-safe order)
        logger.info("Clearing existing data...")
        await session.execute(text("DELETE FROM documents"))
        await session.execute(text("DELETE FROM application_requirements"))
        await session.execute(text("DELETE FROM job_tags"))
        await session.execute(text("DELETE FROM job_search_profile"))
        await session.execute(text("DELETE FROM scrape_runs"))
        await session.execute(text("DELETE FROM jobs"))
        await session.execute(text("DELETE FROM tags"))
        await session.execute(text("DELETE FROM search_profiles"))
        await session.execute(text("DELETE FROM companies"))
        await session.commit()

        # --- Companies ---
        logger.info("Seeding companies...")
        # Shared keyword filters for research/technical roles
        RESEARCH_INCLUDE = [
            "research", "scientist", "engineer", "machine learning",
            "ML", "data science", "AI", "NLP", "policy",
            "technical staff", "applied science", "information scientist",
        ]
        GENERIC_EXCLUDE = [
            "intern", "marketing", "sales", "recruiting", "recruiter",
            "facilities", "office manager", "executive assistant",
            "accounts", "accounting", "payroll", "receptionist",
        ]

        companies_data = [
            {
                "name": "Anthropic",
                "website": "https://anthropic.com",
                "greenhouse_slug": "anthropic",
                "notes": "Tier 1 — Frontier AI lab, AI safety research",
                "include_patterns": RESEARCH_INCLUDE,
                "exclude_patterns": GENERIC_EXCLUDE,
            },
            {
                "name": "Thinking Machines",
                "website": "https://thinkingmachin.es",
                "greenhouse_slug": "thinkingmachines",
                "notes": "Tier 1 — AI/data consultancy",
                "include_patterns": RESEARCH_INCLUDE,
                "exclude_patterns": GENERIC_EXCLUDE,
            },
            {
                "name": "Evolutionary Scale",
                "website": "https://evolutionaryscale.ai",
                "greenhouse_slug": "evolutionaryscale",
                "notes": "Tier 1 — AI for biology",
                "include_patterns": RESEARCH_INCLUDE,
                "exclude_patterns": GENERIC_EXCLUDE,
            },
            {
                "name": "Grammarly",
                "website": "https://grammarly.com",
                "greenhouse_slug": "grammarly",
                "notes": "Tier 2 — AI-powered writing, NLP-heavy",
                "include_patterns": RESEARCH_INCLUDE,
                "exclude_patterns": GENERIC_EXCLUDE,
            },
            {
                "name": "Reka AI",
                "website": "https://reka.ai",
                "ashby_slug": "reka",
                "notes": "Tier 1 — Multimodal AI lab",
                "include_patterns": RESEARCH_INCLUDE,
                "exclude_patterns": GENERIC_EXCLUDE,
            },
            {
                "name": "humans&",
                "website": "https://humansand.com",
                "ashby_slug": "humans-and",
                "notes": "Tier 2 — AI product company",
                "include_patterns": RESEARCH_INCLUDE,
                "exclude_patterns": GENERIC_EXCLUDE,
            },
            {
                "name": "Cohere",
                "website": "https://cohere.com",
                "ashby_slug": "cohere",
                "notes": "Tier 1 — Enterprise LLM provider",
                "include_patterns": RESEARCH_INCLUDE,
                "exclude_patterns": GENERIC_EXCLUDE,
            },
            {
                "name": "OpenAI",
                "website": "https://openai.com",
                "careers_url": "https://openai.com/careers",
                "notes": "Tier 1 — Frontier AI lab (custom career page, no API)",
                "monitoring_active": False,
                "include_patterns": RESEARCH_INCLUDE,
                "exclude_patterns": GENERIC_EXCLUDE,
            },
            # --- More Greenhouse companies ---
            {
                "name": "Scale AI",
                "website": "https://scale.com",
                "greenhouse_slug": "scaleai",
                "notes": "Tier 2 — Data/AI infrastructure",
                "include_patterns": RESEARCH_INCLUDE,
                "exclude_patterns": GENERIC_EXCLUDE,
            },
            {
                "name": "Databricks",
                "website": "https://databricks.com",
                "greenhouse_slug": "databricks",
                "notes": "Tier 2 — Data/AI platform, acquired MosaicML",
                "include_patterns": RESEARCH_INCLUDE,
                "exclude_patterns": GENERIC_EXCLUDE,
            },
            {
                "name": "DeepMind",
                "website": "https://deepmind.google",
                "greenhouse_slug": "deepmind",
                "notes": "Tier 1 — Frontier AI lab (Google)",
                "include_patterns": RESEARCH_INCLUDE,
                "exclude_patterns": GENERIC_EXCLUDE,
            },
            {
                "name": "Stability AI",
                "website": "https://stability.ai",
                "greenhouse_slug": "stabilityai",
                "notes": "Tier 1 — Generative AI (Stable Diffusion)",
                "include_patterns": RESEARCH_INCLUDE,
                "exclude_patterns": GENERIC_EXCLUDE,
            },
            {
                "name": "Contextual AI",
                "website": "https://contextual.ai",
                "greenhouse_slug": "contextualai",
                "notes": "Tier 2 — Enterprise RAG/AI startup",
                "include_patterns": RESEARCH_INCLUDE,
                "exclude_patterns": GENERIC_EXCLUDE,
            },
            {
                "name": "Runway",
                "website": "https://runwayml.com",
                "greenhouse_slug": "runwayml",
                "notes": "Tier 2 — Generative video/creative AI",
                "include_patterns": RESEARCH_INCLUDE,
                "exclude_patterns": GENERIC_EXCLUDE,
            },
            # --- Lever companies ---
            {
                "name": "Mistral AI",
                "website": "https://mistral.ai",
                "lever_slug": "mistral",
                "notes": "Tier 1 — Open-weights LLM lab (Paris)",
                "include_patterns": RESEARCH_INCLUDE,
                "exclude_patterns": GENERIC_EXCLUDE,
            },
        ]
        companies = {}
        for cd in companies_data:
            company = Company(**cd)
            session.add(company)
            companies[cd["name"]] = company
        await session.flush()

        # --- Tags ---
        # Tags are now user-created. Skip seeding default tags to keep the
        # open-source defaults neutral. Users create their own via the UI.
        tags = {}

        # --- Search Profiles ---
        logger.info("Seeding search profiles...")
        sp1 = SearchProfile(
            name="AI Policy Research",
            keywords=["AI policy", "AI governance", "AI safety", "technology policy"],
            locations=["Washington, DC", "Remote"],
            remote_ok=True,
            experience_level="mid-senior",
        )
        sp2 = SearchProfile(
            name="Data Science - Think Tanks",
            keywords=["data science", "research", "quantitative analysis", "policy research"],
            locations=["Washington, DC", "New York, NY"],
            remote_ok=True,
            salary_min=120000,
        )
        session.add_all([sp1, sp2])
        await session.flush()

        # --- Jobs, Application Requirements, Documents ---
        # No sample jobs are seeded. Jobs enter the system via:
        #   1. The scrapers (ATS companies above will be scraped on startup
        #      once monitoring is active)
        #   2. Hacker News "Who's Hiring" threads
        #   3. Manual "Add Job" URL import from the jobs page toolbar
        #   4. The "Find Hot Companies" AI discovery flow

        await session.commit()
        logger.info(
            "Seed complete! Created %d companies, 0 tags (user-created), 2 search profiles.",
            len(companies_data),
        )


if __name__ == "__main__":
    asyncio.run(seed())
