"""Seed script — run with: docker compose exec api python -m app.seed"""

import asyncio
import logging

from sqlalchemy import text

from app.database import async_session
from app.models import (
    ApplicationRequirements,
    Company,
    Document,
    DocType,
    Job,
    JobSource,
    JobStatus,
    SearchProfile,
    Tag,
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
        logger.info("Seeding tags...")
        tags_data = [
            ("AI/ML", "#6366F1"),
            ("Policy", "#EC4899"),
            ("Remote", "#10B981"),
            ("Government", "#3B82F6"),
            ("Think Tank", "#8B5CF6"),
            ("Tech Company", "#F59E0B"),
            ("Data Science", "#06B6D4"),
            ("National Security", "#EF4444"),
            ("Economics", "#84CC16"),
            ("Hybrid", "#F97316"),
        ]
        tags = {}
        for name, color in tags_data:
            tag = Tag(name=name, color=color)
            session.add(tag)
            tags[name] = tag
        await session.flush()

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

        # --- Jobs ---
        logger.info("Seeding jobs...")
        jobs_data = [
            {
                "title": "AI Policy Research Scientist",
                "company": "RAND Corporation",
                "location": "Washington, DC",
                "remote": False,
                "salary_min": 110000,
                "salary_max": 145000,
                "description": "Conduct research on artificial intelligence policy implications for national security and governance. Publish findings in peer-reviewed journals and brief policymakers.",
                "url": "https://rand.org/jobs/ai-policy-research-scientist",
                "source": JobSource.manual,
                "status": JobStatus.interested,
                "relevance_score": 0.92,
                "thumbs": 1,
                "tag_names": ["AI/ML", "Policy", "Think Tank", "National Security"],
                "search_profiles": [sp1],
            },
            {
                "title": "Sr Research Scientist - AI Governance",
                "company": "Brookings Institution",
                "location": "Washington, DC",
                "remote": False,
                "salary_min": 105000,
                "salary_max": 140000,
                "description": "Lead research initiatives on AI governance frameworks. Collaborate with policy teams to develop recommendations for responsible AI deployment in government and industry.",
                "url": "https://brookings.edu/jobs/sr-research-scientist-ai-governance",
                "source": JobSource.greenhouse,
                "status": JobStatus.new,
                "relevance_score": 0.88,
                "thumbs": 1,
                "tag_names": ["AI/ML", "Policy", "Think Tank"],
                "search_profiles": [sp1],
            },
            {
                "title": "Research Scientist - Responsible AI",
                "company": "Anthropic",
                "company_id": companies["Anthropic"].id,
                "location": "San Francisco, CA",
                "remote": True,
                "salary_min": 180000,
                "salary_max": 250000,
                "description": "Research AI alignment and safety. Develop evaluation frameworks for large language models and contribute to responsible scaling practices.",
                "url": "https://anthropic.com/jobs/research-scientist-responsible-ai",
                "source": JobSource.greenhouse,
                "status": JobStatus.applied,
                "relevance_score": 0.91,
                "thumbs": 1,
                "tag_names": ["AI/ML", "Tech Company", "Remote"],
                "search_profiles": [sp1],
            },
            {
                "title": "AI Policy Analyst",
                "company": "Georgetown CSET",
                "location": "Washington, DC",
                "remote": False,
                "salary_min": 85000,
                "salary_max": 115000,
                "description": "Analyze emerging AI technologies and their policy implications. Write policy briefs and support congressional briefings on technology governance.",
                "url": "https://cset.georgetown.edu/jobs/ai-policy-analyst",
                "source": JobSource.manual,
                "status": JobStatus.new,
                "relevance_score": 0.85,
                "thumbs": None,
                "tag_names": ["AI/ML", "Policy", "Think Tank"],
                "search_profiles": [sp1],
            },
            {
                "title": "Data Scientist - National Security",
                "company": "MITRE",
                "location": "McLean, VA",
                "remote": False,
                "salary_min": 120000,
                "salary_max": 160000,
                "description": "Apply data science methods to national security challenges. Build models for threat assessment and decision support systems.",
                "url": "https://mitre.org/jobs/data-scientist-national-security",
                "source": JobSource.greenhouse,
                "status": JobStatus.new,
                "relevance_score": 0.78,
                "thumbs": None,
                "tag_names": ["Data Science", "National Security", "Government"],
                "search_profiles": [sp2],
            },
            {
                "title": "Quantitative Researcher",
                "company": "Federal Reserve Board",
                "location": "Washington, DC",
                "remote": False,
                "salary_min": 130000,
                "salary_max": 175000,
                "description": "Conduct quantitative economic research supporting monetary policy decisions. Build econometric models and analyze large-scale financial datasets.",
                "url": "https://federalreserve.gov/jobs/quantitative-researcher",
                "source": JobSource.linkedin,
                "status": JobStatus.new,
                "relevance_score": 0.65,
                "thumbs": None,
                "tag_names": ["Data Science", "Economics", "Government"],
                "search_profiles": [sp2],
            },
            {
                "title": "Machine Learning Engineer",
                "company": "Tech Startup",
                "location": "San Francisco, CA",
                "remote": True,
                "salary_min": 160000,
                "salary_max": 220000,
                "description": "Build and deploy ML models at scale. Work on recommendation systems and natural language processing pipelines.",
                "url": "https://techstartup.io/jobs/ml-engineer",
                "source": JobSource.greenhouse,
                "status": JobStatus.new,
                "relevance_score": 0.55,
                "thumbs": None,
                "tag_names": ["AI/ML", "Tech Company", "Remote"],
                "search_profiles": [],
            },
            {
                "title": "Policy Advisor - Digital Economy",
                "company": "OECD",
                "location": "Paris, France",
                "remote": False,
                "salary_min": 95000,
                "salary_max": 130000,
                "description": "Advise member countries on digital economy policy. Draft policy recommendations on AI, data governance, and digital trade.",
                "url": "https://oecd.org/jobs/policy-advisor-digital-economy",
                "source": JobSource.manual,
                "status": JobStatus.new,
                "relevance_score": 0.60,
                "thumbs": None,
                "tag_names": ["Policy", "Economics"],
                "search_profiles": [sp1],
            },
            {
                "title": "Senior Data Analyst",
                "company": "Deloitte",
                "location": "Arlington, VA",
                "remote": False,
                "salary_min": 95000,
                "salary_max": 135000,
                "description": "Analyze government data sets for federal consulting engagements. Develop dashboards and automated reporting solutions.",
                "url": "https://deloitte.com/jobs/senior-data-analyst",
                "source": JobSource.linkedin,
                "status": JobStatus.new,
                "relevance_score": 0.50,
                "thumbs": None,
                "tag_names": ["Data Science", "Government"],
                "search_profiles": [sp2],
            },
            {
                "title": "Frontend Developer",
                "company": "Random Startup",
                "location": "Austin, TX",
                "remote": True,
                "salary_min": 100000,
                "salary_max": 140000,
                "description": "Build React-based web applications for a SaaS product. Focus on component architecture and performance optimization.",
                "url": "https://randomstartup.com/jobs/frontend-developer",
                "source": JobSource.greenhouse,
                "status": JobStatus.archived,
                "relevance_score": 0.20,
                "thumbs": -1,
                "tag_names": ["Tech Company", "Remote"],
                "search_profiles": [],
            },
            {
                "title": "Marketing Data Analyst",
                "company": "CPG Corp",
                "location": "Chicago, IL",
                "remote": False,
                "salary_min": 70000,
                "salary_max": 95000,
                "description": "Analyze marketing campaign performance and consumer behavior data. Build attribution models and optimize ad spend.",
                "url": "https://cpgcorp.com/jobs/marketing-data-analyst",
                "source": JobSource.linkedin,
                "status": JobStatus.new,
                "relevance_score": 0.25,
                "thumbs": -1,
                "tag_names": ["Data Science"],
                "search_profiles": [],
            },
            {
                "title": "Junior Research Assistant",
                "company": "University",
                "location": "Boston, MA",
                "remote": False,
                "salary_min": 45000,
                "salary_max": 55000,
                "description": "Support faculty research on political science topics. Assist with literature reviews, data collection, and manuscript preparation.",
                "url": "https://university.edu/jobs/junior-research-assistant",
                "source": JobSource.manual,
                "status": JobStatus.new,
                "relevance_score": 0.15,
                "thumbs": -1,
                "tag_names": ["Policy"],
                "search_profiles": [],
            },
        ]

        job_objects = []
        for jd in jobs_data:
            tag_names = jd.pop("tag_names")
            search_profiles = jd.pop("search_profiles")
            job = Job(**jd)
            job.tags = [tags[name] for name in tag_names]
            job.search_profiles = search_profiles
            session.add(job)
            job_objects.append(job)

        await session.flush()

        # --- Application Requirements ---
        logger.info("Seeding application requirements...")

        # Anthropic (job index 2)
        session.add(ApplicationRequirements(
            job_id=job_objects[2].id,
            needs_resume=True,
            needs_cover_letter=True,
            needs_short_answers=True,
            short_answer_prompts={
                "prompts": [
                    "Why are you interested in AI safety research?",
                    "Describe a research project where you had to balance competing objectives.",
                ]
            },
        ))

        # RAND (job index 0)
        session.add(ApplicationRequirements(
            job_id=job_objects[0].id,
            needs_resume=True,
            needs_cover_letter=True,
        ))

        # Georgetown CSET (job index 3)
        session.add(ApplicationRequirements(
            job_id=job_objects[3].id,
            needs_resume=True,
            needs_cover_letter=True,
            needs_short_answers=True,
            short_answer_prompts={
                "prompts": [
                    "What emerging AI policy issue do you think deserves more attention?",
                ]
            },
        ))

        await session.flush()

        # --- Documents ---
        logger.info("Seeding documents...")

        # Base resume template
        session.add(Document(
            job_id=None,
            doc_type=DocType.resume,
            name="Base Resume Template",
            content_markdown="# Resume\n\n## Summary\n\n[AI-generated summary placeholder]\n\n## Experience\n\n[Pulled from accomplishments]\n\n## Education\n\n[From profile]\n\n## Skills\n\n[From profile]",
            is_base_template=True,
            version=1,
        ))

        # Tailored resume for Anthropic
        session.add(Document(
            job_id=job_objects[2].id,
            doc_type=DocType.resume,
            name="Resume - Anthropic Research Scientist",
            content_markdown="# Resume — Tailored for Anthropic\n\n## Summary\nResearcher with experience in AI policy and governance, seeking to apply analytical skills to AI safety research.\n\n## Experience\n[Tailored bullet points]\n\n## Skills\nAI Safety, Policy Analysis, Python, Machine Learning",
            is_base_template=False,
            version=1,
        ))

        # Cover letter for Anthropic
        session.add(Document(
            job_id=job_objects[2].id,
            doc_type=DocType.cover_letter,
            name="Cover Letter - Anthropic Research Scientist",
            content_markdown="# Cover Letter\n\nDear Hiring Team,\n\nI am writing to express my interest in the Research Scientist - Responsible AI position at Anthropic...\n\n[AI-generated content placeholder]",
            is_base_template=False,
            version=1,
        ))

        await session.commit()
        logger.info(
            "Seed complete! Created %d companies, 10 tags, 2 search profiles, "
            "12 jobs, 3 app requirements, 3 documents.",
            len(companies_data),
        )


if __name__ == "__main__":
    asyncio.run(seed())
