"""Synthetic test cases for scoring evaluation.

16 jobs organized into 5 tiers with ground-truth score ranges,
pairwise ordering constraints, and deal-breaker expectations.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScoringTestCase:
    case_id: str
    tier: str  # "perfect" | "good" | "mediocre" | "mismatch" | "edge"
    job: dict  # title, company, description, salary_min, salary_max, user_notes

    # Expected score ranges (inclusive)
    role_fit_range: tuple[int, int]
    interest_fit_range: tuple[int, int]

    # Deal-breaker expectations
    has_deal_breaker: bool = False
    deal_breaker_keyword: str | None = None

    # Pairwise ordering: must_beat case_ids on composite score
    must_beat: list[str] = field(default_factory=list)

    # What failure mode this tests
    failure_mode: str | None = None

    @property
    def expected_composite_range(self) -> tuple[float, float]:
        """Derived composite range from 0.6*role + 0.4*interest."""
        low = 0.6 * self.role_fit_range[0] + 0.4 * self.interest_fit_range[0]
        high = 0.6 * self.role_fit_range[1] + 0.4 * self.interest_fit_range[1]
        return (low, high)


# ---------------------------------------------------------------------------
# Fixed few-shot examples (not loaded from DB — ensures reproducibility)
# ---------------------------------------------------------------------------

POSITIVE_EXAMPLES = [
    {
        "title": "Research Scientist, NLP",
        "company": "Cohere",
        "location": "Remote",
        "description": "Work on large language model optimization and applied NLP research. Publish at top venues, ship production systems. Python, PyTorch, transformers.",
        "salary_min": 180000,
        "salary_max": 250000,
        "role_fit_score": 88,
        "interest_fit_score": 92,
    },
    {
        "title": "Applied AI Researcher",
        "company": "Anthropic",
        "location": "San Francisco, CA",
        "description": "Develop novel techniques for making AI systems more helpful and harmless. Focus on RLHF, interpretability, and production deployment. Strong publishing culture.",
        "salary_min": 200000,
        "salary_max": 300000,
        "role_fit_score": 91,
        "interest_fit_score": 95,
    },
    {
        "title": "Senior ML Scientist, Foundation Models",
        "company": "Allen Institute for AI (AI2)",
        "location": "Seattle, WA",
        "description": "Lead research on open foundation models. Collaborate with academic partners, publish openly, build tools used by researchers worldwide. Python, JAX/PyTorch.",
        "salary_min": 170000,
        "salary_max": 230000,
        "role_fit_score": 85,
        "interest_fit_score": 90,
    },
]

NEGATIVE_EXAMPLES = [
    {
        "title": "Data Analyst, Marketing",
        "company": "Criteo",
        "location": "New York, NY",
        "description": "Analyze ad campaign performance and optimize click-through rates. SQL, Tableau, A/B testing for display advertising.",
        "salary_min": 85000,
        "salary_max": 110000,
        "role_fit_score": 25,
        "interest_fit_score": 8,
    },
    {
        "title": "Software Engineer, Defense Systems",
        "company": "Raytheon Technologies",
        "location": "Tucson, AZ",
        "description": "Build software for missile guidance systems. C++, real-time systems, security clearance required.",
        "salary_min": 120000,
        "salary_max": 160000,
        "role_fit_score": 20,
        "interest_fit_score": 3,
    },
    {
        "title": "Junior Business Analyst",
        "company": "Deloitte",
        "location": "Chicago, IL",
        "description": "Support consulting engagements with data analysis and slide preparation. Excel, PowerPoint, client communication.",
        "salary_min": 65000,
        "salary_max": 85000,
        "role_fit_score": 10,
        "interest_fit_score": 5,
    },
]


def _format_few_shot(examples: list[dict]) -> str:
    """Format example job dicts into few-shot text (mirrors format_example_jobs)."""
    parts = []
    for i, ex in enumerate(examples, 1):
        lines = [f"{i}. {ex['title']} at {ex['company']}"]
        if ex.get("location"):
            lines[0] += f" ({ex['location']})"
        if ex.get("salary_min") or ex.get("salary_max"):
            salary = ""
            if ex.get("salary_min"):
                salary += f"${ex['salary_min']:,}"
            if ex.get("salary_max"):
                salary += f" - ${ex['salary_max']:,}"
            lines.append(f"   Salary: {salary}")
        score_parts = []
        if ex.get("role_fit_score") is not None:
            score_parts.append(f"role_fit={ex['role_fit_score']}")
        if ex.get("interest_fit_score") is not None:
            score_parts.append(f"interest_fit={ex['interest_fit_score']}")
        if score_parts:
            lines.append(f"   Prior scores: {', '.join(score_parts)}")
        desc = ex.get("description", "")[:300]
        if desc:
            lines.append(f"   Description snippet: {desc}...")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


POSITIVE_EXAMPLES_TEXT = _format_few_shot(POSITIVE_EXAMPLES)
NEGATIVE_EXAMPLES_TEXT = _format_few_shot(NEGATIVE_EXAMPLES)


# ---------------------------------------------------------------------------
# Profile data (mirrors live DB profile with new free-text format)
# ---------------------------------------------------------------------------

EVAL_PROFILE_DATA: dict = {
    "personal": {
        "name": "Test Candidate",
        "location": "Washington, DC",
        "remote_preference": "remote-first",
        "willing_to_relocate": True,
    },
    "experience_years": 5,
    "target_roles": [
        {"title": "Research Scientist", "seniority": "Senior"},
        {"title": "Applied Scientist", "seniority": "Senior"},
        {"title": "Research Engineer", "seniority": "Senior"},
        {"title": "ML Scientist", "seniority": "Mid-Senior"},
    ],
    "domains": [
        "AI/ML",
        "natural language processing",
        "foundation models",
        "computational social science",
        "AI governance",
        "labor economics",
    ],
    "skills": {
        "technical": [
            "python",
            "pytorch",
            "tensorflow",
            "nlp",
            "llm",
            "transformer architectures",
            "causal inference",
            "statistics",
            "machine learning",
            "deep learning",
            "time series analysis",
            "reinforcement learning",
        ],
        "communication": [
            "academic publishing",
            "technical writing",
            "policy briefs",
            "stakeholder presentations",
        ],
        "tools": [
            "huggingface",
            "wandb",
            "docker",
            "aws",
            "git",
            "sql",
            "spark",
        ],
    },
    "work_history": [
        {
            "title": "Research Scientist",
            "employer": "Applied AI Lab",
            "start": "2022",
            "end": None,
        },
        {
            "title": "Data Scientist",
            "employer": "Policy Research Institute",
            "start": "2019",
            "end": "2022",
        },
    ],
    "education": [
        {
            "degree": "PhD",
            "field": "Computational Social Science",
            "institution": "Top University",
            "year": 2019,
            "honors": "Dissertation Award",
        },
        {
            "degree": "BS",
            "field": "Computer Science & Economics",
            "institution": "State University",
            "year": 2014,
        },
    ],
    "awards": [
        "Best Paper Award, NLP Conference 2023",
        "NSF Graduate Research Fellowship",
    ],
    "complete_profile": {
        "accomplishments": [
            {
                "employer": "Applied AI Lab",
                "title": "Led LLM optimization project",
                "work_history_key": "applied_ai_lab",
                "impact_summary": "Reduced inference latency by 40% for production LLM serving 10M+ users",
                "quantitative_specifics": ["40% latency reduction", "10M+ users"],
                "skills_demonstrated": ["pytorch", "llm", "optimization", "production ml"],
                "relevance_weight": 0.95,
            },
            {
                "employer": "Applied AI Lab",
                "title": "Built human-centered AI evaluation framework",
                "work_history_key": "applied_ai_lab",
                "impact_summary": "Designed and deployed evaluation framework combining automated metrics with human judgment",
                "quantitative_specifics": ["50+ evaluators", "3 product teams adopted"],
                "skills_demonstrated": ["nlp", "human-centered design", "evaluation"],
                "relevance_weight": 0.9,
            },
            {
                "employer": "Policy Research Institute",
                "title": "Causal analysis of labor market disruption from AI",
                "work_history_key": "policy_institute",
                "impact_summary": "Published influential study on AI's impact on knowledge worker employment",
                "quantitative_specifics": ["200+ citations", "Congressional testimony"],
                "skills_demonstrated": ["causal inference", "statistics", "python", "policy"],
                "relevance_weight": 0.8,
            },
        ],
        "publications": [
            {
                "title": "Efficient Fine-tuning of Large Language Models for Domain Adaptation",
                "venue": "ACL",
                "year": 2023,
                "first_author": True,
                "relevance_weight": 0.95,
            },
            {
                "title": "Human-Centered Evaluation of AI-Generated Text",
                "venue": "CHI",
                "year": 2023,
                "first_author": True,
                "relevance_weight": 0.9,
            },
        ],
    },
    "search_preferences": {
        "looking_for": (
            "I'm looking for a role that involves cutting edge AI research, largely "
            "within language models but also interested in time series foundation models "
            "and other platforms. I ideally would like this research to be in the private "
            "sector but not opposed to other non-profit think tanks as well. I want this "
            "research to serve a real product that people use - I want to build something "
            "that has to work, not just look/sound impressive."
        ),
        "not_looking_for": (
            "I don't want to be involved in existential risk type of AI research. While "
            "I prefer private sector jobs, I don't want to build products that feel "
            "soul-less to me - ad optimizers, defense contracting, general consulting, etc."
        ),
        "positive_signals": [
            "applied ai research",
            "language models",
            "llm optimization",
            "time series models",
            "foundation models",
            "production ml",
            "human-centered design",
            "human-centered-ai",
            "research engineering",
            "publish",
        ],
        "exclusions": [
            "existential risk",
            "ad optimization",
            "advertising",
            "defense contracting",
            "general consulting",
            "weapons development",
        ],
    },
}


# ---------------------------------------------------------------------------
# 16 Synthetic Test Cases
# ---------------------------------------------------------------------------

TEST_CASES: list[ScoringTestCase] = [
    # ===== TIER A: Perfect Matches (expected composite 85+) =====
    ScoringTestCase(
        case_id="A1",
        tier="perfect",
        job={
            "title": "Senior Research Scientist, LLM Systems",
            "company": "Mosaic AI",
            "description": (
                "We're building the next generation of production LLM tools and "
                "infrastructure. As a Senior Research Scientist, you'll lead research on "
                "LLM optimization — from efficient fine-tuning to novel inference "
                "strategies — and ship your work into products used by thousands of "
                "engineering teams.\n\n"
                "Responsibilities:\n"
                "- Design and implement novel methods for LLM efficiency (quantization, "
                "distillation, speculative decoding)\n"
                "- Collaborate with product teams to deploy research into production\n"
                "- Publish at top ML venues (NeurIPS, ICML, ACL)\n"
                "- Mentor junior researchers\n\n"
                "Requirements:\n"
                "- PhD in CS, ML, NLP, or related field\n"
                "- 3+ years post-PhD research experience\n"
                "- Strong Python, PyTorch proficiency\n"
                "- Track record of publications at top venues\n"
                "- Experience deploying ML models at scale\n\n"
                "We're a small team (30 people) with high ownership and a strong "
                "publishing culture. Every researcher ships code to production."
            ),
            "salary_min": 200000,
            "salary_max": 280000,
            "user_notes": None,
        },
        role_fit_range=(80, 100),
        interest_fit_range=(80, 100),
        must_beat=["B4", "B5", "B6", "B7", "C8", "C9", "C10", "C11", "D12", "D13", "D14"],
    ),
    ScoringTestCase(
        case_id="A2",
        tier="perfect",
        job={
            "title": "Staff Research Engineer, Foundation Models",
            "company": "Anthropic",
            "description": (
                "Join Anthropic's Foundation Models team to push the frontier of AI "
                "capability and safety. You will work on pre-training, post-training, "
                "and inference optimization for Claude — our production AI assistant "
                "used by millions.\n\n"
                "What you'll do:\n"
                "- Research and implement improvements to LLM training pipelines\n"
                "- Develop novel techniques for model efficiency and capability\n"
                "- Collaborate across research and engineering to ship improvements\n"
                "- Contribute to published research and open-source tools\n\n"
                "Requirements:\n"
                "- PhD or equivalent experience in ML/AI\n"
                "- Strong background in deep learning, transformers, NLP\n"
                "- Python, PyTorch/JAX, distributed training experience\n"
                "- Published research in ML/NLP venues\n\n"
                "Compensation: $200K-$350K + equity. SF-based or remote-friendly."
            ),
            "salary_min": 200000,
            "salary_max": 350000,
            "user_notes": None,
        },
        role_fit_range=(80, 100),
        interest_fit_range=(80, 100),
        must_beat=["B4", "B5", "B6", "B7", "C8", "C9", "C10", "C11", "D12", "D13", "D14"],
    ),
    ScoringTestCase(
        case_id="A3",
        tier="perfect",
        job={
            "title": "Senior Applied Scientist, Human-Centered AI",
            "company": "Hugging Face",
            "description": (
                "We're looking for a Senior Applied Scientist to lead our "
                "human-centered AI research initiative. You'll work at the "
                "intersection of LLM capabilities and user experience, developing "
                "evaluation frameworks and interaction paradigms that make AI systems "
                "genuinely useful.\n\n"
                "The role:\n"
                "- Lead research on human-AI interaction and evaluation\n"
                "- Build and ship tools used by the ML community worldwide\n"
                "- Publish research and contribute to open-source\n"
                "- Design experiments combining automated metrics with human judgment\n\n"
                "You bring:\n"
                "- PhD in HCI, NLP, ML, or related\n"
                "- Experience with LLMs and NLP systems\n"
                "- Strong Python/ML engineering skills\n"
                "- Publications in CHI, ACL, EMNLP, or similar\n"
                "- Passion for making AI accessible and useful\n\n"
                "Fully remote. Competitive salary + equity."
            ),
            "salary_min": 180000,
            "salary_max": 260000,
            "user_notes": None,
        },
        role_fit_range=(78, 100),
        interest_fit_range=(80, 100),
        must_beat=["B4", "B5", "B6", "C8", "C9", "C10", "C11", "D12", "D13", "D14"],
    ),

    # ===== TIER B: Good Matches (expected composite 55-80) =====
    ScoringTestCase(
        case_id="B4",
        tier="good",
        job={
            "title": "Research Scientist, Time Series Foundation Models",
            "company": "MIT Media Lab",
            "description": (
                "The MIT Media Lab seeks a Research Scientist to work on foundation "
                "models for time series data. You'll develop novel architectures for "
                "multi-modal temporal modeling, with applications in climate science, "
                "healthcare, and economics.\n\n"
                "Responsibilities:\n"
                "- Develop and evaluate foundation models for time series\n"
                "- Publish at NeurIPS, ICML, ICLR\n"
                "- Supervise graduate students\n"
                "- Collaborate with domain experts across MIT\n\n"
                "Requirements:\n"
                "- PhD in ML, Statistics, or related\n"
                "- Strong publication record\n"
                "- Python, PyTorch, experience with sequence models\n\n"
                "This is a 3-year research appointment. Salary: $95K-$130K."
            ),
            "salary_min": 95000,
            "salary_max": 130000,
            "user_notes": None,
        },
        role_fit_range=(65, 90),
        interest_fit_range=(45, 75),
        must_beat=["C8", "C9", "C10", "C11", "D12", "D13", "D14"],
        failure_mode="academic_salary_product_gap",
    ),
    ScoringTestCase(
        case_id="B5",
        tier="good",
        job={
            "title": "Senior Data Scientist, Applied ML",
            "company": "Tempus AI",
            "description": (
                "Tempus uses AI to transform healthcare. As a Senior Data Scientist, "
                "you'll develop ML models for clinical decision support — from NLP on "
                "clinical notes to causal inference on treatment outcomes.\n\n"
                "What you'll do:\n"
                "- Build and deploy ML models for clinical applications\n"
                "- Apply causal inference methods to observational health data\n"
                "- Collaborate with clinicians and product teams\n"
                "- Contribute to peer-reviewed publications\n\n"
                "Requirements:\n"
                "- PhD or MS + 5 years in quantitative field\n"
                "- Python, SQL, ML frameworks\n"
                "- Experience with NLP or causal inference\n"
                "- Healthcare domain experience preferred\n\n"
                "Salary: $160K-$210K. Chicago or remote."
            ),
            "salary_min": 160000,
            "salary_max": 210000,
            "user_notes": None,
        },
        role_fit_range=(50, 85),
        interest_fit_range=(40, 70),
        must_beat=["C8", "C10", "C11", "D12", "D13", "D14"],
        failure_mode="domain_mismatch_skill_match",
    ),
    ScoringTestCase(
        case_id="B6",
        tier="good",
        job={
            "title": "AI Policy Researcher",
            "company": "Brookings Institution",
            "description": (
                "The Brookings Institution's Center on AI seeks an AI Policy "
                "Researcher to produce rigorous analysis of AI governance, labor "
                "market impacts, and technology regulation.\n\n"
                "Responsibilities:\n"
                "- Author policy briefs and reports on AI governance\n"
                "- Conduct quantitative research on AI's economic impacts\n"
                "- Present findings to policymakers and media\n"
                "- Organize convenings with industry and civil society\n\n"
                "Requirements:\n"
                "- PhD in economics, political science, CS, or related\n"
                "- Published research on technology policy\n"
                "- Strong quantitative methods\n"
                "- Experience with causal inference preferred\n\n"
                "Salary: $110K-$145K. Washington, DC (hybrid)."
            ),
            "salary_min": 110000,
            "salary_max": 145000,
            "user_notes": None,
        },
        role_fit_range=(55, 80),
        interest_fit_range=(35, 65),
        must_beat=["C8", "C11", "D12", "D13", "D14"],
        failure_mode="background_vs_interest_divergence",
    ),
    ScoringTestCase(
        case_id="B7",
        tier="good",
        job={
            "title": "Senior ML Engineer, Production LLM Infrastructure",
            "company": "Databricks",
            "description": (
                "Build the infrastructure that powers LLM training and serving at "
                "scale. You'll work on distributed training, model serving, and the "
                "MLOps platform used by thousands of companies.\n\n"
                "What you'll do:\n"
                "- Design and implement distributed LLM training infrastructure\n"
                "- Optimize model serving for latency and throughput\n"
                "- Build tools for LLM fine-tuning and evaluation\n"
                "- Collaborate with research scientists on production deployment\n\n"
                "Requirements:\n"
                "- 5+ years ML engineering experience\n"
                "- Strong Python, distributed systems (Spark, Ray)\n"
                "- Experience with LLM training/serving\n"
                "- Comfortable with both research and engineering\n\n"
                "Salary: $190K-$270K + equity. SF or remote."
            ),
            "salary_min": 190000,
            "salary_max": 270000,
            "user_notes": None,
        },
        role_fit_range=(55, 80),
        interest_fit_range=(45, 78),
        must_beat=["C8", "C10", "C11", "D12", "D13", "D14"],
        failure_mode="engineering_vs_research",
    ),

    # ===== TIER C: Mediocre Matches (expected composite 30-55) =====
    ScoringTestCase(
        case_id="C8",
        tier="mediocre",
        job={
            "title": "Data Scientist",
            "company": "Stripe",
            "description": (
                "Join Stripe's data science team to build models for fraud "
                "detection, revenue optimization, and user behavior analysis.\n\n"
                "What you'll do:\n"
                "- Build and deploy ML models for payments risk\n"
                "- Analyze user behavior and product metrics\n"
                "- Collaborate with product and engineering teams\n\n"
                "Requirements:\n"
                "- MS or PhD in quantitative field\n"
                "- 2-4 years experience\n"
                "- Python, SQL, ML fundamentals\n"
                "- Experience with A/B testing and causal inference a plus\n\n"
                "Salary: $140K-$180K. SF or remote."
            ),
            "salary_min": 140000,
            "salary_max": 180000,
            "user_notes": None,
        },
        role_fit_range=(35, 60),
        interest_fit_range=(20, 45),
        must_beat=["D12", "D13", "D14"],
        failure_mode="generic_ds_wrong_seniority",
    ),
    ScoringTestCase(
        case_id="C9",
        tier="mediocre",
        job={
            "title": "Research Fellow, AI Existential Risk",
            "company": "Centre for AI Safety",
            "description": (
                "The Centre for AI Safety seeks a Research Fellow to work on "
                "reducing existential risk from advanced AI systems. You'll study "
                "alignment failure modes, develop safety benchmarks, and produce "
                "research aimed at ensuring transformative AI benefits humanity.\n\n"
                "Responsibilities:\n"
                "- Conduct technical AI safety research\n"
                "- Develop methods for detecting deceptive alignment\n"
                "- Publish at ML safety workshops and conferences\n"
                "- Engage with the AI safety research community\n\n"
                "Requirements:\n"
                "- PhD in ML, CS, or related\n"
                "- Strong understanding of deep learning and LLMs\n"
                "- Research experience in alignment, interpretability, or robustness\n\n"
                "Salary: $120K-$160K. Remote-friendly."
            ),
            "salary_min": 120000,
            "salary_max": 160000,
            "user_notes": None,
        },
        role_fit_range=(50, 80),
        interest_fit_range=(5, 35),
        has_deal_breaker=True,
        deal_breaker_keyword="existential risk",
        must_beat=["D12", "D13", "D14"],
        failure_mode="exclusion_despite_skill_match",
    ),
    ScoringTestCase(
        case_id="C10",
        tier="mediocre",
        job={
            "title": "ML Engineer, Recommendations",
            "company": "Netflix",
            "description": (
                "Help build Netflix's recommendation engine, serving 250M+ members "
                "worldwide. You'll develop deep learning models for content "
                "personalization, search ranking, and user engagement optimization.\n\n"
                "What you'll do:\n"
                "- Train and deploy recommendation models at scale\n"
                "- Optimize engagement and retention metrics\n"
                "- Run A/B tests on algorithmic changes\n"
                "- Collaborate with content and product teams\n\n"
                "Requirements:\n"
                "- MS/PhD in ML, CS, or related\n"
                "- Strong Python, TensorFlow/PyTorch\n"
                "- Experience with recommender systems or information retrieval\n"
                "- 3+ years ML engineering experience\n\n"
                "Salary: $180K-$250K. Los Gatos, CA."
            ),
            "salary_min": 180000,
            "salary_max": 250000,
            "user_notes": None,
        },
        role_fit_range=(45, 70),
        interest_fit_range=(15, 40),
        must_beat=["D12", "D13", "D14"],
        failure_mode="optimization_no_research",
    ),
    ScoringTestCase(
        case_id="C11",
        tier="mediocre",
        job={
            "title": "Junior Data Analyst",
            "company": "U.S. Government Accountability Office",
            "description": (
                "The GAO is hiring a Junior Data Analyst to support audits of "
                "federal AI programs. You'll analyze government AI spending, assess "
                "agency AI readiness, and contribute to reports for Congress.\n\n"
                "Responsibilities:\n"
                "- Collect and analyze data on federal AI initiatives\n"
                "- Write sections of GAO reports\n"
                "- Support senior analysts in policy evaluations\n\n"
                "Requirements:\n"
                "- BA/BS in data science, economics, or related\n"
                "- 0-2 years experience\n"
                "- Proficiency in Excel, SQL, basic Python\n"
                "- Interest in government technology policy\n\n"
                "Salary: $55K-$75K (GS-9). Washington, DC."
            ),
            "salary_min": 55000,
            "salary_max": 75000,
            "user_notes": None,
        },
        role_fit_range=(10, 35),
        interest_fit_range=(10, 35),
        must_beat=["D12", "D13", "D14"],
        failure_mode="wrong_seniority",
    ),

    # ===== TIER D: Clear Mismatches (expected composite <25) =====
    ScoringTestCase(
        case_id="D12",
        tier="mismatch",
        job={
            "title": "Senior Frontend Engineer",
            "company": "Shopify",
            "description": (
                "Build beautiful, performant e-commerce experiences. You'll work "
                "on Shopify's storefront using React, TypeScript, and GraphQL.\n\n"
                "Requirements:\n"
                "- 5+ years frontend development\n"
                "- Expert in React, TypeScript, CSS\n"
                "- Experience with GraphQL and performance optimization\n"
                "- Eye for design and user experience\n\n"
                "Salary: $170K-$220K. Remote."
            ),
            "salary_min": 170000,
            "salary_max": 220000,
            "user_notes": None,
        },
        role_fit_range=(0, 20),
        interest_fit_range=(0, 20),
    ),
    ScoringTestCase(
        case_id="D13",
        tier="mismatch",
        job={
            "title": "Growth Marketing Manager",
            "company": "The Trade Desk",
            "description": (
                "Lead growth marketing for the world's leading independent "
                "demand-side ad platform. You'll optimize programmatic advertising "
                "campaigns, manage ad spend, and drive customer acquisition through "
                "digital advertising channels.\n\n"
                "Requirements:\n"
                "- 5+ years in digital marketing / ad optimization\n"
                "- Experience with DSPs, programmatic advertising\n"
                "- Strong analytical skills, Google Analytics, Tableau\n"
                "- Track record of scaling acquisition channels\n\n"
                "Salary: $130K-$170K. New York, NY."
            ),
            "salary_min": 130000,
            "salary_max": 170000,
            "user_notes": None,
        },
        role_fit_range=(0, 15),
        interest_fit_range=(0, 10),
        has_deal_breaker=True,
        deal_breaker_keyword="ad optimization",
    ),
    ScoringTestCase(
        case_id="D14",
        tier="mismatch",
        job={
            "title": "Senior Software Engineer",
            "company": "Raytheon Technologies",
            "description": (
                "Develop mission-critical software for advanced defense systems. "
                "You'll build real-time data processing pipelines and integration "
                "software for radar and electronic warfare systems.\n\n"
                "Requirements:\n"
                "- 5+ years software engineering (Python, C++)\n"
                "- Active TS/SCI security clearance\n"
                "- Experience with real-time systems\n"
                "- Background in signal processing a plus\n\n"
                "Salary: $140K-$190K. Tucson, AZ."
            ),
            "salary_min": 140000,
            "salary_max": 190000,
            "user_notes": None,
        },
        role_fit_range=(5, 25),
        interest_fit_range=(0, 10),
        has_deal_breaker=True,
        deal_breaker_keyword="defense contracting",
    ),

    # ===== TIER E: Edge Cases =====
    ScoringTestCase(
        case_id="E15",
        tier="edge",
        job={
            "title": "Research Scientist",
            "company": "Pfizer AI Lab",
            "description": (
                "Pfizer's AI Lab is seeking a Research Scientist to apply machine "
                "learning to drug discovery. You'll develop novel architectures for "
                "molecular property prediction, protein structure modeling, and "
                "clinical trial optimization.\n\n"
                "Responsibilities:\n"
                "- Develop ML models for drug candidate screening\n"
                "- Apply NLP to biomedical literature mining\n"
                "- Collaborate with medicinal chemists and biologists\n"
                "- Publish in bioinformatics and ML venues\n\n"
                "Requirements:\n"
                "- PhD in ML, computational biology, or related\n"
                "- Strong Python, PyTorch, experience with GNNs\n"
                "- Publications in ML or computational biology\n\n"
                "Salary: $150K-$200K. New York, NY or Cambridge, MA."
            ),
            "salary_min": 150000,
            "salary_max": 200000,
            "user_notes": None,
        },
        role_fit_range=(40, 70),
        interest_fit_range=(25, 55),
        must_beat=["D12", "D13", "D14"],
        failure_mode="title_seduction",
    ),
    ScoringTestCase(
        case_id="E16",
        tier="edge",
        job={
            "title": "Senior Research Scientist, AI-Powered Ad Optimization",
            "company": "Meta",
            "description": (
                "Join Meta's Ads Ranking team as a Senior Research Scientist. "
                "You'll develop state-of-the-art deep learning models to optimize "
                "ad delivery across Facebook and Instagram, improving relevance and "
                "engagement for 3B+ users.\n\n"
                "What you'll do:\n"
                "- Research novel architectures for large-scale ad ranking\n"
                "- Develop LLM-based approaches for ad creative understanding\n"
                "- Publish at top ML venues (KDD, RecSys, NeurIPS)\n"
                "- Optimize click-through rate and conversion prediction\n\n"
                "Requirements:\n"
                "- PhD in ML, NLP, or related\n"
                "- 3+ years research experience\n"
                "- Strong Python, PyTorch, distributed training\n"
                "- Published research in recommendations or NLP\n\n"
                "Salary: $220K-$320K + RSUs. Menlo Park, CA or remote."
            ),
            "salary_min": 220000,
            "salary_max": 320000,
            "user_notes": None,
        },
        role_fit_range=(55, 85),
        interest_fit_range=(0, 25),
        has_deal_breaker=True,
        deal_breaker_keyword="ad optimization",
        failure_mode="perfect_title_exclusion_domain",
    ),
]

# Build lookup dict
TEST_CASES_BY_ID: dict[str, ScoringTestCase] = {tc.case_id: tc for tc in TEST_CASES}

# Tier groupings
TIERS = {
    "perfect": [tc for tc in TEST_CASES if tc.tier == "perfect"],
    "good": [tc for tc in TEST_CASES if tc.tier == "good"],
    "mediocre": [tc for tc in TEST_CASES if tc.tier == "mediocre"],
    "mismatch": [tc for tc in TEST_CASES if tc.tier == "mismatch"],
    "edge": [tc for tc in TEST_CASES if tc.tier == "edge"],
}
