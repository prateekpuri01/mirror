"""Benchmark profiles and job descriptions for resume generation evaluation.

Extends the base EVAL_PROFILE_DATA with richer accomplishments and publications,
then defines 6 test cases across 3 tiers (perfect_match, stretch, challenge) to
exercise tailoring logic, accomplishment selection, and pitfall avoidance.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from tests.eval.fixtures import EVAL_PROFILE_DATA

# ---------------------------------------------------------------------------
# Extended profile for resume evaluation
# ---------------------------------------------------------------------------

RESUME_EVAL_PROFILE: dict = copy.deepcopy(EVAL_PROFILE_DATA)

RESUME_EVAL_PROFILE["complete_profile"] = {
    "accomplishments": [
        {
            "id": "ACC_llm_opt",
            "category": "research",
            "employer": "Applied AI Lab",
            "work_history_key": "applied_ai_lab",
            "title": "Led production LLM inference optimization initiative",
            "date_range": "2023-01 – 2024-06",
            "impact_summary": (
                "Designed and shipped a speculative-decoding pipeline that reduced "
                "p99 inference latency by 40% for a production LLM serving 10M+ "
                "daily active users, saving $1.2M/year in compute costs."
            ),
            "quantitative_specifics": [
                "40% p99 latency reduction",
                "10M+ daily active users",
                "$1.2M annual compute savings",
            ],
            "so_what": (
                "Demonstrated that cutting-edge efficiency research can ship to "
                "production at scale, bridging the gap between ML theory and "
                "real-world deployment."
            ),
            "skills_demonstrated": [
                "pytorch",
                "llm",
                "optimization",
                "production ml",
                "speculative decoding",
                "distributed systems",
            ],
            "hands_on_work": (
                "Wrote custom CUDA kernels for KV-cache management, built A/B "
                "testing framework for latency experiments, authored internal "
                "design docs adopted as team standard."
            ),
            "relevance_weight": 0.95,
            "tags": ["llm", "production", "optimization", "systems"],
            "related_publication_ids": ["PUB_finetune"],
        },
        {
            "id": "ACC_hcai_eval",
            "category": "research",
            "employer": "Applied AI Lab",
            "work_history_key": "applied_ai_lab",
            "title": "Built human-centered AI evaluation framework",
            "date_range": "2022-06 – 2023-06",
            "impact_summary": (
                "Designed and deployed an evaluation framework combining automated "
                "metrics with structured human judgment, adopted by 3 product teams "
                "and used to evaluate 200+ model iterations."
            ),
            "quantitative_specifics": [
                "50+ evaluators trained",
                "3 product teams adopted framework",
                "200+ model iterations evaluated",
            ],
            "so_what": (
                "Showed that human-centered evaluation catches failure modes that "
                "automated metrics miss, directly improving user satisfaction "
                "scores by 18%."
            ),
            "skills_demonstrated": [
                "nlp",
                "human-centered design",
                "evaluation",
                "experiment design",
                "user research",
            ],
            "hands_on_work": (
                "Built annotation platform in Python/React, designed rubrics, "
                "ran inter-annotator agreement studies, integrated with CI/CD "
                "pipeline for automated eval gating."
            ),
            "relevance_weight": 0.90,
            "tags": ["hcai", "evaluation", "nlp", "user-research"],
            "related_publication_ids": ["PUB_hcai"],
        },
        {
            "id": "ACC_rlhf",
            "category": "research",
            "employer": "Applied AI Lab",
            "work_history_key": "applied_ai_lab",
            "title": "Developed RLHF pipeline for domain-specific alignment",
            "date_range": "2023-06 – 2024-03",
            "impact_summary": (
                "Built an end-to-end RLHF training pipeline that aligned a "
                "7B-parameter LLM to domain-specific preferences, reducing harmful "
                "outputs by 65% while maintaining task performance."
            ),
            "quantitative_specifics": [
                "65% reduction in harmful outputs",
                "7B-parameter model",
                "15K preference pairs collected",
            ],
            "so_what": (
                "Proved that RLHF can be applied cost-effectively to smaller "
                "models for domain-specific safety, without the massive data "
                "budgets typically assumed necessary."
            ),
            "skills_demonstrated": [
                "reinforcement learning",
                "llm",
                "alignment",
                "pytorch",
                "distributed training",
            ],
            "hands_on_work": (
                "Implemented PPO training loop with custom reward model, managed "
                "preference data collection with crowdworkers, built evaluation "
                "harness for safety benchmarks."
            ),
            "relevance_weight": 0.92,
            "tags": ["rlhf", "alignment", "safety", "llm"],
            "related_publication_ids": ["PUB_rlhf"],
        },
        {
            "id": "ACC_labor_causal",
            "category": "research",
            "employer": "Policy Research Institute",
            "work_history_key": "policy_institute",
            "title": "Causal analysis of AI-driven labor market disruption",
            "date_range": "2020-01 – 2022-03",
            "impact_summary": (
                "Published influential study using difference-in-differences and "
                "synthetic control methods to estimate AI's causal impact on "
                "knowledge worker employment, cited 200+ times and referenced "
                "in Congressional testimony."
            ),
            "quantitative_specifics": [
                "200+ citations",
                "Congressional testimony",
                "3 follow-up studies commissioned",
            ],
            "so_what": (
                "Provided the first rigorous causal evidence of AI's labor market "
                "effects, shifting policy debate from speculation to data-driven "
                "analysis."
            ),
            "skills_demonstrated": [
                "causal inference",
                "statistics",
                "python",
                "policy analysis",
                "econometrics",
            ],
            "hands_on_work": (
                "Cleaned and linked BLS/Census microdata, implemented synthetic "
                "control estimator in Python, wrote policy briefs for "
                "non-technical audiences."
            ),
            "relevance_weight": 0.80,
            "tags": ["causal-inference", "labor-economics", "policy"],
            "related_publication_ids": ["PUB_labor"],
        },
        {
            "id": "ACC_timeseries",
            "category": "research",
            "employer": "Policy Research Institute",
            "work_history_key": "policy_institute",
            "title": "Developed transformer-based time series forecasting model",
            "date_range": "2021-03 – 2022-06",
            "impact_summary": (
                "Adapted transformer architectures for multivariate economic time "
                "series forecasting, achieving state-of-the-art results on 4 "
                "benchmark datasets and deploying the model for real-time economic "
                "indicator tracking."
            ),
            "quantitative_specifics": [
                "SOTA on 4 benchmarks",
                "12% MAPE improvement over prior best",
                "Real-time dashboard serving 500+ analysts",
            ],
            "so_what": (
                "Demonstrated that foundation model architectures transfer "
                "effectively to structured time series data, opening a new "
                "research direction at the institute."
            ),
            "skills_demonstrated": [
                "time series analysis",
                "transformer architectures",
                "pytorch",
                "forecasting",
                "deployment",
            ],
            "hands_on_work": (
                "Implemented custom attention masking for temporal dependencies, "
                "built data pipeline for streaming economic indicators, deployed "
                "model behind FastAPI service."
            ),
            "relevance_weight": 0.75,
            "tags": ["time-series", "transformers", "forecasting", "deployment"],
            "related_publication_ids": ["PUB_timeseries"],
        },
        {
            "id": "ACC_nlp_policy",
            "category": "research",
            "employer": "Policy Research Institute",
            "work_history_key": "policy_institute",
            "title": "NLP pipeline for large-scale policy document analysis",
            "date_range": "2019-06 – 2021-01",
            "impact_summary": (
                "Built an NLP system to classify and extract key provisions from "
                "50K+ federal regulatory documents, enabling automated tracking "
                "of AI governance trends across agencies."
            ),
            "quantitative_specifics": [
                "50K+ documents processed",
                "92% classification F1",
                "Used by 3 federal agencies",
            ],
            "so_what": (
                "Turned unstructured policy text into structured, searchable data — "
                "showing how NLP can make governance more transparent and efficient."
            ),
            "skills_demonstrated": [
                "nlp",
                "text classification",
                "python",
                "information extraction",
                "data engineering",
            ],
            "hands_on_work": (
                "Fine-tuned BERT models for multi-label classification, built "
                "named entity recognition for regulatory entities, designed "
                "PostgreSQL schema for document metadata."
            ),
            "relevance_weight": 0.70,
            "tags": ["nlp", "policy", "information-extraction", "classification"],
            "related_publication_ids": ["PUB_policy_nlp"],
        },
        {
            "id": "ACC_mentorship",
            "category": "leadership",
            "employer": "Applied AI Lab",
            "work_history_key": "applied_ai_lab",
            "title": "Established junior researcher mentorship program",
            "date_range": "2023-01 – present",
            "impact_summary": (
                "Created and led a structured mentorship program for 8 junior "
                "researchers, resulting in 5 first-author publications and 2 "
                "successful promotions within 18 months."
            ),
            "quantitative_specifics": [
                "8 mentees",
                "5 first-author publications",
                "2 promotions",
            ],
            "so_what": (
                "Built a culture of research excellence and retention, reducing "
                "junior researcher attrition from 30% to 10%."
            ),
            "skills_demonstrated": [
                "mentorship",
                "leadership",
                "research management",
                "technical writing",
            ],
            "relevance_weight": 0.60,
            "tags": ["leadership", "mentorship", "culture"],
            "related_publication_ids": [],
        },
        {
            "id": "ACC_open_source",
            "category": "engineering",
            "employer": "Applied AI Lab",
            "work_history_key": "applied_ai_lab",
            "title": "Open-source LLM evaluation toolkit",
            "date_range": "2023-09 – 2024-04",
            "impact_summary": (
                "Created and maintained an open-source toolkit for LLM evaluation "
                "that reached 2.5K GitHub stars, adopted by 12 organizations "
                "including 3 Fortune 500 companies."
            ),
            "quantitative_specifics": [
                "2.5K GitHub stars",
                "12 organizations adopted",
                "45 external contributors",
            ],
            "so_what": (
                "Established the lab's reputation in the open-source community "
                "and created a shared standard for LLM evaluation that reduced "
                "duplicated effort across the industry."
            ),
            "skills_demonstrated": [
                "python",
                "open source",
                "llm",
                "evaluation",
                "software engineering",
                "community building",
            ],
            "hands_on_work": (
                "Designed plugin architecture, wrote comprehensive docs, managed "
                "PR reviews from external contributors, set up CI/CD with "
                "benchmark regression tests."
            ),
            "relevance_weight": 0.85,
            "tags": ["open-source", "llm", "evaluation", "engineering"],
            "related_publication_ids": ["PUB_hcai"],
        },
        {
            "id": "ACC_multimodal",
            "category": "research",
            "employer": "Applied AI Lab",
            "work_history_key": "applied_ai_lab",
            "title": "Multimodal retrieval system for enterprise search",
            "date_range": "2024-01 – 2024-06",
            "impact_summary": (
                "Designed a multimodal retrieval-augmented generation system "
                "combining text, table, and image understanding, deployed as the "
                "core search engine for an enterprise knowledge base serving "
                "15K employees."
            ),
            "quantitative_specifics": [
                "15K employees served",
                "35% improvement in search relevance (MRR@10)",
                "60% reduction in support tickets",
            ],
            "so_what": (
                "Proved that multimodal RAG can work reliably at enterprise scale, "
                "directly reducing operational costs and improving knowledge "
                "worker productivity."
            ),
            "skills_demonstrated": [
                "retrieval augmented generation",
                "multimodal ml",
                "llm",
                "information retrieval",
                "production ml",
            ],
            "hands_on_work": (
                "Built custom embedding pipeline for heterogeneous documents, "
                "implemented hybrid dense/sparse retrieval, fine-tuned vision "
                "encoder for table understanding."
            ),
            "relevance_weight": 0.88,
            "tags": ["rag", "multimodal", "search", "production"],
            "related_publication_ids": [],
        },
    ],
    "publications": [
        {
            "id": "PUB_finetune",
            "title": "Efficient Fine-tuning of Large Language Models for Domain Adaptation",
            "authors": ["Test Candidate", "A. Collaborator", "B. Advisor"],
            "venue": "ACL",
            "year": 2023,
            "type": "conference",
            "first_author": True,
            "work_history_key": "applied_ai_lab",
            "impact_summary": (
                "Introduced a parameter-efficient fine-tuning method that matches "
                "full fine-tuning performance at 8% of the compute cost."
            ),
            "abstract": (
                "We propose LoRA-X, a parameter-efficient fine-tuning method for "
                "large language models that achieves parity with full fine-tuning "
                "on 6 domain-adaptation benchmarks while using only 8% of the "
                "training compute. Our approach selectively adapts attention heads "
                "based on gradient-informed importance scoring, enabling effective "
                "domain transfer with minimal catastrophic forgetting."
            ),
            "skills_demonstrated": ["llm", "fine-tuning", "pytorch", "nlp"],
            "relevance_weight": 0.95,
        },
        {
            "id": "PUB_hcai",
            "title": "Human-Centered Evaluation of AI-Generated Text: A Multi-Dimensional Framework",
            "authors": ["Test Candidate", "C. Designer", "D. Psychologist"],
            "venue": "CHI",
            "year": 2023,
            "type": "conference",
            "first_author": True,
            "work_history_key": "applied_ai_lab",
            "impact_summary": (
                "Proposed a structured evaluation framework combining automated "
                "metrics with human judgment across 5 quality dimensions, now "
                "adopted by 3 major AI labs."
            ),
            "abstract": (
                "Current evaluation of AI-generated text relies heavily on "
                "automated metrics that correlate poorly with human preferences. "
                "We introduce HEVAL, a multi-dimensional framework that structures "
                "human evaluation across factuality, coherence, helpfulness, "
                "safety, and style. In a study with 150 evaluators across 3 "
                "domains, HEVAL achieves 0.85 inter-annotator agreement and "
                "predicts downstream user satisfaction 2x better than BLEU/ROUGE."
            ),
            "skills_demonstrated": [
                "human-centered design",
                "evaluation",
                "nlp",
                "user research",
            ],
            "relevance_weight": 0.90,
        },
        {
            "id": "PUB_rlhf",
            "title": "Cost-Effective RLHF for Domain-Specific Language Model Alignment",
            "authors": ["Test Candidate", "E. Engineer", "F. Researcher"],
            "venue": "NeurIPS",
            "year": 2024,
            "type": "conference",
            "first_author": True,
            "work_history_key": "applied_ai_lab",
            "impact_summary": (
                "Showed that RLHF can align smaller models to domain-specific "
                "safety requirements using 10x fewer preference pairs than "
                "standard approaches."
            ),
            "abstract": (
                "Reinforcement learning from human feedback (RLHF) is typically "
                "applied to large frontier models with massive preference datasets. "
                "We demonstrate that targeted preference collection combined with "
                "curriculum-based reward model training enables effective alignment "
                "of 7B-parameter models using only 15K preference pairs. Our "
                "approach reduces harmful outputs by 65% while maintaining 98% of "
                "baseline task performance across 3 specialized domains."
            ),
            "skills_demonstrated": [
                "reinforcement learning",
                "alignment",
                "llm",
                "pytorch",
            ],
            "relevance_weight": 0.92,
        },
        {
            "id": "PUB_labor",
            "title": "The Causal Impact of AI Adoption on Knowledge Worker Employment",
            "authors": ["Test Candidate", "G. Economist"],
            "venue": "American Economic Review",
            "year": 2022,
            "type": "journal",
            "first_author": True,
            "work_history_key": "policy_institute",
            "impact_summary": (
                "First rigorous causal estimate of AI's employment effects using "
                "synthetic control methods, cited in Congressional testimony."
            ),
            "abstract": (
                "We estimate the causal effect of AI tool adoption on knowledge "
                "worker employment using a synthetic control approach across 48 "
                "metropolitan areas from 2015-2022. Regions with early AI adoption "
                "experienced a 4.2% relative decline in routine cognitive "
                "employment, offset by a 2.8% increase in non-routine analytical "
                "roles. Our findings suggest net displacement is concentrated in "
                "middle-skill occupations, with implications for retraining policy."
            ),
            "skills_demonstrated": [
                "causal inference",
                "econometrics",
                "statistics",
                "policy analysis",
            ],
            "relevance_weight": 0.80,
        },
        {
            "id": "PUB_timeseries",
            "title": "Temporal Transformers: Attention-Based Architectures for Economic Time Series",
            "authors": ["Test Candidate", "H. Statistician", "I. Economist"],
            "venue": "ICML",
            "year": 2022,
            "type": "conference",
            "first_author": True,
            "work_history_key": "policy_institute",
            "impact_summary": (
                "Adapted transformer architectures for multivariate economic time "
                "series, achieving SOTA on 4 benchmarks with 12% MAPE improvement."
            ),
            "abstract": (
                "We introduce Temporal Transformer, an attention-based architecture "
                "designed for multivariate economic time series forecasting. Unlike "
                "standard transformers, our model incorporates temporal positional "
                "encodings calibrated to economic cycles, cross-series attention "
                "for capturing macroeconomic dependencies, and a hierarchical "
                "aggregation mechanism for multi-horizon prediction. On 4 "
                "benchmark datasets spanning GDP, employment, inflation, and trade "
                "flows, we achieve 12% average MAPE improvement over prior SOTA."
            ),
            "skills_demonstrated": [
                "time series analysis",
                "transformer architectures",
                "pytorch",
                "forecasting",
            ],
            "relevance_weight": 0.75,
        },
        {
            "id": "PUB_policy_nlp",
            "title": "Automated Analysis of Federal AI Governance: An NLP Approach",
            "authors": ["Test Candidate", "J. Political Scientist"],
            "venue": "EMNLP",
            "year": 2021,
            "type": "conference",
            "first_author": True,
            "work_history_key": "policy_institute",
            "impact_summary": (
                "Applied NLP to 50K+ federal documents for automated governance "
                "tracking, adopted by 3 federal agencies."
            ),
            "abstract": (
                "We present a pipeline for automated analysis of federal "
                "regulatory documents related to artificial intelligence. Using "
                "fine-tuned BERT models for multi-label classification and custom "
                "named entity recognition for regulatory actors, our system "
                "processes 50K+ documents with 92% F1, enabling real-time tracking "
                "of AI governance trends across federal agencies. We release the "
                "annotated dataset and models to support future policy research."
            ),
            "skills_demonstrated": [
                "nlp",
                "text classification",
                "information extraction",
                "python",
            ],
            "relevance_weight": 0.70,
        },
        {
            "id": "PUB_survey",
            "title": "A Survey of Evaluation Methods for Large Language Models",
            "authors": ["K. Postdoc", "Test Candidate", "L. Professor"],
            "venue": "Transactions on Machine Learning Research (TMLR)",
            "year": 2024,
            "type": "journal",
            "first_author": False,
            "work_history_key": "applied_ai_lab",
            "impact_summary": (
                "Comprehensive survey covering 150+ evaluation approaches for "
                "LLMs, establishing a taxonomy widely adopted in the field."
            ),
            "abstract": (
                "The rapid proliferation of large language models has outpaced "
                "the development of evaluation methodology. We survey 150+ "
                "evaluation approaches across 6 dimensions: capability, safety, "
                "alignment, robustness, efficiency, and societal impact. Our "
                "taxonomy identifies critical gaps in current practice and "
                "proposes a framework for holistic LLM evaluation that balances "
                "automated metrics, human judgment, and real-world deployment "
                "monitoring."
            ),
            "skills_demonstrated": [
                "llm",
                "evaluation",
                "technical writing",
                "literature review",
            ],
            "relevance_weight": 0.65,
        },
    ],
}


# ---------------------------------------------------------------------------
# Resume test case dataclass
# ---------------------------------------------------------------------------


@dataclass
class ResumeTestCase:
    case_id: str
    case_name: str
    tier: str  # "perfect_match" | "stretch" | "challenge"
    job: dict  # title, company, description (200-400 words), salary_min, salary_max
    expected_accomplishment_ids: list[str] = field(default_factory=list)
    expected_pitfalls: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 6 Test Cases (2 perfect_match, 2 stretch, 2 challenge)
# ---------------------------------------------------------------------------

RESUME_TEST_CASES: list[ResumeTestCase] = [
    # ===== TIER: perfect_match =====
    ResumeTestCase(
        case_id="RS_llm_opt",
        case_name="Senior Research Scientist, LLM Systems — production AI lab",
        tier="perfect_match",
        job={
            "title": "Senior Research Scientist, LLM Systems",
            "company": "Mosaic AI",
            "description": (
                "Mosaic AI is building the next generation of production LLM "
                "infrastructure. We are looking for a Senior Research Scientist "
                "to lead research on LLM optimization — from efficient fine-tuning "
                "and inference acceleration to novel training paradigms — and ship "
                "that work into products used by thousands of engineering teams.\n\n"
                "Responsibilities:\n"
                "- Design and implement novel methods for LLM efficiency including "
                "quantization, distillation, and speculative decoding\n"
                "- Collaborate with product engineering to deploy research into "
                "production systems handling 100M+ daily requests\n"
                "- Publish at top ML venues (NeurIPS, ICML, ACL) and contribute "
                "to open-source projects\n"
                "- Mentor junior researchers and help shape the team's research "
                "agenda\n\n"
                "Requirements:\n"
                "- PhD in Computer Science, Machine Learning, NLP, or related field\n"
                "- 3+ years post-PhD research experience with production ML\n"
                "- Strong Python and PyTorch proficiency; experience with "
                "distributed training frameworks\n"
                "- Track record of publications at top venues and shipped "
                "production systems\n"
                "- Experience with LLM fine-tuning, RLHF, or inference "
                "optimization strongly preferred\n\n"
                "We are a small team (30 people) with high ownership and a strong "
                "publishing culture. Every researcher ships code to production. "
                "Salary: $200K-$280K + equity. San Francisco or remote."
            ),
            "salary_min": 200000,
            "salary_max": 280000,
        },
        expected_accomplishment_ids=[
            "ACC_llm_opt",
            "ACC_rlhf",
            "ACC_open_source",
            "ACC_multimodal",
            "ACC_mentorship",
        ],
        expected_pitfalls=[
            "Should not lead with policy or economics accomplishments",
            "Should not over-emphasize time series work that is not relevant",
            "Should not omit production deployment experience",
            "Should not fabricate systems engineering accomplishments not in profile",
        ],
    ),
    ResumeTestCase(
        case_id="RS_hcai",
        case_name="Senior Applied Scientist, Human-Centered AI — research company",
        tier="perfect_match",
        job={
            "title": "Senior Applied Scientist, Human-Centered AI",
            "company": "Hugging Face",
            "description": (
                "Hugging Face is looking for a Senior Applied Scientist to lead "
                "our human-centered AI research initiative. You will work at the "
                "intersection of LLM capabilities and user experience, developing "
                "evaluation frameworks and interaction paradigms that make AI "
                "systems genuinely useful for real people.\n\n"
                "The role:\n"
                "- Lead research on human-AI interaction, evaluation methodology, "
                "and user-centered design of AI tools\n"
                "- Build and ship open-source tools used by the ML community "
                "worldwide, contributing to the Hugging Face ecosystem\n"
                "- Publish research at top HCI and NLP venues (CHI, ACL, EMNLP) "
                "and present at industry conferences\n"
                "- Design and run experiments combining automated metrics with "
                "structured human judgment to evaluate model quality\n\n"
                "You bring:\n"
                "- PhD in HCI, NLP, Machine Learning, or a related field\n"
                "- Demonstrated experience building and evaluating LLM-based "
                "systems with real users\n"
                "- Strong Python and ML engineering skills; experience with the "
                "Hugging Face ecosystem a plus\n"
                "- First-author publications in CHI, ACL, EMNLP, or similar "
                "venues\n"
                "- Passion for making AI tools accessible, usable, and genuinely "
                "helpful\n\n"
                "Fully remote. Competitive salary + equity."
            ),
            "salary_min": 180000,
            "salary_max": 260000,
        },
        expected_accomplishment_ids=[
            "ACC_hcai_eval",
            "ACC_open_source",
            "ACC_rlhf",
            "ACC_llm_opt",
            "ACC_mentorship",
        ],
        expected_pitfalls=[
            "Should not lead with inference optimization over human-centered work",
            "Should not omit open-source experience which is central to HF culture",
            "Should not over-emphasize policy or economics background",
            "Should not fabricate UX design credentials not in the profile",
        ],
    ),
    # ===== TIER: stretch =====
    ResumeTestCase(
        case_id="RS_timeseries",
        case_name="Research Scientist, Time Series Foundation Models — adjacent domain",
        tier="stretch",
        job={
            "title": "Research Scientist, Time Series Foundation Models",
            "company": "Amazon Science",
            "description": (
                "Amazon Science is seeking a Research Scientist to join our "
                "newly formed Time Series Foundation Models team. You will develop "
                "novel transformer-based architectures for universal time series "
                "representation learning, with applications spanning demand "
                "forecasting, anomaly detection, and causal discovery in temporal "
                "data.\n\n"
                "Responsibilities:\n"
                "- Design and train foundation models for heterogeneous time "
                "series data at scale (billions of time points)\n"
                "- Develop self-supervised pre-training objectives tailored to "
                "temporal structure and cross-domain transfer\n"
                "- Publish at top ML venues (NeurIPS, ICML, ICLR) and "
                "collaborate with applied science teams across Amazon\n"
                "- Build scalable training infrastructure on AWS and contribute "
                "to open-source releases\n\n"
                "Requirements:\n"
                "- PhD in Machine Learning, Statistics, or a related field\n"
                "- Strong publication record in time series, sequence modeling, "
                "or foundation models\n"
                "- Deep experience with transformer architectures and "
                "self-supervised learning\n"
                "- Python, PyTorch, and distributed training at scale\n"
                "- Experience with forecasting, anomaly detection, or causal "
                "inference in temporal data is a strong plus\n\n"
                "Salary: $180K-$260K + RSUs. Seattle, WA or Arlington, VA."
            ),
            "salary_min": 180000,
            "salary_max": 260000,
        },
        expected_accomplishment_ids=[
            "ACC_timeseries",
            "ACC_llm_opt",
            "ACC_multimodal",
            "ACC_labor_causal",
            "ACC_open_source",
        ],
        expected_pitfalls=[
            "Should not overstate depth of time series experience — candidate "
            "has one project, not a career focus",
            "Should not ignore transferable transformer and foundation model skills from LLM work",
            "Should not omit causal inference experience which is listed as a plus",
            "Should not fabricate forecasting results beyond what is in the profile",
        ],
    ),
    ResumeTestCase(
        case_id="RS_policy",
        case_name="AI Policy Researcher — think tank, wrong role type",
        tier="stretch",
        job={
            "title": "AI Policy Researcher",
            "company": "Brookings Institution",
            "description": (
                "The Brookings Institution's Center on Artificial Intelligence "
                "seeks an AI Policy Researcher to produce rigorous, data-driven "
                "analysis of AI governance, labor market impacts, and technology "
                "regulation. This role bridges technical AI expertise and public "
                "policy, translating complex research findings for policymakers, "
                "journalists, and the public.\n\n"
                "Responsibilities:\n"
                "- Author policy briefs, white papers, and peer-reviewed articles "
                "on AI governance, workforce impacts, and regulatory frameworks\n"
                "- Conduct original quantitative research on AI's economic and "
                "social effects using causal inference and survey methods\n"
                "- Present findings to Congressional staff, federal agencies, and "
                "international organizations\n"
                "- Organize and moderate convenings with industry leaders, civil "
                "society, and academic researchers\n"
                "- Engage with media and produce public-facing commentary on AI "
                "policy developments\n\n"
                "Requirements:\n"
                "- PhD in economics, political science, computer science, or a "
                "related field\n"
                "- Published research on technology policy or AI governance\n"
                "- Strong quantitative methods (causal inference, econometrics)\n"
                "- Excellent writing for both technical and general audiences\n"
                "- Experience engaging with policymakers preferred\n\n"
                "Salary: $110K-$145K. Washington, DC (hybrid)."
            ),
            "salary_min": 110000,
            "salary_max": 145000,
        },
        expected_accomplishment_ids=[
            "ACC_labor_causal",
            "ACC_nlp_policy",
            "ACC_hcai_eval",
        ],
        expected_pitfalls=[
            "Should not lead with LLM systems engineering — this is a policy role",
            "Should not position candidate as a pure technologist",
            "Should not ignore the significant salary reduction compared to "
            "current compensation tier",
            "Should not fabricate policy engagement experience beyond "
            "Congressional testimony already in profile",
        ],
    ),
    # ===== TIER: challenge =====
    ResumeTestCase(
        case_id="RS_sparse",
        case_name="ML Researcher at stealth startup — sparse description",
        tier="challenge",
        job={
            "title": "ML Researcher",
            "company": "Stealth AI Startup (Series A)",
            "description": (
                "We are a well-funded Series A startup building foundation models "
                "for a new modality. We are hiring an ML Researcher to join our "
                "core research team. You will have significant autonomy to define "
                "and pursue research directions. Strong publication record and "
                "hands-on engineering skills required. PhD preferred. Competitive "
                "compensation with significant equity. San Francisco. This role "
                "requires someone who can both write papers and write production "
                "code — we do not separate research and engineering. Our team "
                "includes alumni from Google Brain, DeepMind, and OpenAI. If you "
                "thrive in ambiguity and want to shape a company's technical "
                "direction from the ground floor, we want to hear from you."
            ),
            "salary_min": None,
            "salary_max": None,
        },
        expected_accomplishment_ids=[
            "ACC_llm_opt",
            "ACC_rlhf",
            "ACC_open_source",
            "ACC_multimodal",
            "ACC_hcai_eval",
        ],
        expected_pitfalls=[
            "Should not over-tailor to a specific domain since the JD is vague — "
            "resume should be broad",
            "Should not omit production engineering experience which is explicitly required",
            "Should not assume the 'new modality' and fabricate relevant experience",
            "Should not ignore the startup context — emphasize autonomy and "
            "breadth, not just depth",
        ],
    ),
    ResumeTestCase(
        case_id="RS_dealbreaker",
        case_name="Senior Research Scientist, AI-Powered Ad Optimization — deal-breaker domain",
        tier="challenge",
        job={
            "title": "Senior Research Scientist, AI-Powered Ad Optimization",
            "company": "Meta",
            "description": (
                "Join Meta's Ads Ranking team as a Senior Research Scientist. "
                "You will develop state-of-the-art deep learning models to "
                "optimize ad delivery across Facebook, Instagram, and Threads, "
                "improving ad relevance and engagement for 3B+ users worldwide.\n\n"
                "What you will do:\n"
                "- Research novel neural architectures for large-scale ad ranking "
                "and click-through rate prediction\n"
                "- Develop LLM-based approaches for ad creative understanding, "
                "matching, and generation\n"
                "- Publish at top ML venues (KDD, RecSys, NeurIPS, WWW) and "
                "contribute to Meta's research reputation\n"
                "- Optimize revenue-critical models serving billions of ad "
                "impressions daily\n"
                "- Collaborate with product and engineering teams to ship model "
                "improvements that move business metrics\n\n"
                "Requirements:\n"
                "- PhD in Machine Learning, NLP, Information Retrieval, or a "
                "related field\n"
                "- 3+ years of research experience in ranking, recommendations, "
                "or NLP\n"
                "- Strong Python, PyTorch, and distributed training experience\n"
                "- Published research in recommendations, NLP, or related areas\n"
                "- Experience with large-scale production ML systems\n\n"
                "This role is on the Ads Ranking team — you will directly "
                "optimize advertising effectiveness and revenue. Salary: "
                "$220K-$320K + RSUs. Menlo Park, CA or NYC (remote negotiable)."
            ),
            "salary_min": 220000,
            "salary_max": 320000,
        },
        expected_accomplishment_ids=[
            "ACC_llm_opt",
            "ACC_multimodal",
            "ACC_rlhf",
        ],
        expected_pitfalls=[
            "Should flag ad optimization as a deal-breaker per candidate's exclusion preferences",
            "Should not enthusiastically tailor resume to this role without "
            "surfacing the values conflict",
            "Should not fabricate interest in advertising or engagement optimization",
            "Should not ignore that the role is technically a strong skill "
            "match — the conflict is values, not capability",
        ],
    ),
]


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

RESUME_TEST_CASES_BY_ID: dict[str, ResumeTestCase] = {tc.case_id: tc for tc in RESUME_TEST_CASES}
