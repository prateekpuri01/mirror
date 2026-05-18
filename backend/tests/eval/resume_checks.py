"""Deterministic quality checks for generated resume JSON.

Each check is a pure function: check_X(resume_json, profile_data?) -> list[CheckResult].
No LLM calls. Fast enough to run on every saved resume.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    check_name: str
    severity: str  # "error" | "warning"
    location: str  # e.g., "experience.rand.bullets.2" or "summary"
    message: str
    offending_text: str = ""


@dataclass
class ResumeCheckReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def errors(self) -> list[CheckResult]:
        return [r for r in self.results if r.severity == "error"]

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if r.severity == "warning"]

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    def __add__(self, other: ResumeCheckReport) -> ResumeCheckReport:
        return ResumeCheckReport(results=self.results + other.results)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Content fields to check (excludes internal metadata)
_CONTENT_FIELDS = {"tagline", "summary", "selected_research", "experience",
                   "publications", "technical_skills", "awards"}


def _iter_content_texts(resume: dict) -> list[tuple[str, str]]:
    """Yield (location, text) pairs for all content fields in the resume."""
    pairs: list[tuple[str, str]] = []

    if resume.get("summary"):
        pairs.append(("summary", resume["summary"]))

    if resume.get("tagline"):
        pairs.append(("tagline", resume["tagline"]))

    for i, entry in enumerate(resume.get("selected_research", [])):
        if entry.get("description"):
            pairs.append((f"selected_research.{i}.description", entry["description"]))

    for emp_key, emp_data in resume.get("experience", {}).items():
        for bi, bullet in enumerate(emp_data.get("bullets", [])):
            text = bullet["text"] if isinstance(bullet, dict) else bullet
            pairs.append((f"experience.{emp_key}.bullets.{bi}", text))

    if resume.get("awards"):
        pairs.append(("awards", resume["awards"]))

    return pairs


def _iter_bullet_texts(resume: dict) -> list[tuple[str, str]]:
    """Yield (location, text) for experience bullets only.

    Handles both old (string) and new (dict with "text") bullet formats.
    """
    pairs: list[tuple[str, str]] = []
    for emp_key, emp_data in resume.get("experience", {}).items():
        for bi, bullet in enumerate(emp_data.get("bullets", [])):
            text = bullet["text"] if isinstance(bullet, dict) else bullet
            pairs.append((f"experience.{emp_key}.bullets.{bi}", text))
    return pairs


def _iter_description_texts(resume: dict) -> list[tuple[str, str]]:
    """Yield (location, text) for selected_research descriptions."""
    pairs: list[tuple[str, str]] = []
    for i, entry in enumerate(resume.get("selected_research", [])):
        if entry.get("description"):
            pairs.append((f"selected_research.{i}.description", entry["description"]))
    return pairs


# ---------------------------------------------------------------------------
# Check 1: Word counts
# ---------------------------------------------------------------------------

def check_word_counts(resume: dict) -> list[CheckResult]:
    """Flag sentences exceeding word limits: 25 for bullets, 30 for descriptions, 60 for summary."""
    results: list[CheckResult] = []

    # Bullets: max 35 words per sentence (generous — natural writing can be longer)
    for loc, text in _iter_bullet_texts(resume):
        for sentence in _SENTENCE_SPLIT.split(text):
            sentence = sentence.strip()
            if not sentence:
                continue
            wc = len(sentence.split())
            if wc > 35:
                results.append(CheckResult(
                    check_name="word_count",
                    severity="warning",
                    location=loc,
                    message=f"Bullet sentence has {wc} words (max 35)",
                    offending_text=sentence,
                ))

    # Descriptions: min 75 words total, max 30 words per sentence
    for loc, text in _iter_description_texts(resume):
        total_wc = len(text.split())
        if total_wc < 75:
            results.append(CheckResult(
                check_name="word_count",
                severity="error",
                location=loc,
                message=f"Research description has {total_wc} words (min 75)",
                offending_text=text[:80] + "...",
            ))
        for sentence in _SENTENCE_SPLIT.split(text):
            sentence = sentence.strip()
            if not sentence:
                continue
            wc = len(sentence.split())
            if wc > 30:
                results.append(CheckResult(
                    check_name="word_count",
                    severity="error",
                    location=loc,
                    message=f"Description sentence has {wc} words (max 30)",
                    offending_text=sentence,
                ))

    # Summary: max 60 words total
    summary = resume.get("summary", "")
    if summary:
        wc = len(summary.split())
        if wc > 60:
            results.append(CheckResult(
                check_name="word_count",
                severity="warning",
                location="summary",
                message=f"Summary has {wc} words (max 60)",
                offending_text=summary[:100] + "...",
            ))

    return results


# ---------------------------------------------------------------------------
# Check 2: Pronoun violations
# ---------------------------------------------------------------------------

_PRONOUN_PATTERN = re.compile(
    r"\b(I|my|me|he|she|his|her|they|their|we|our)\b", re.IGNORECASE
)
# "I" needs case-sensitive check to avoid matching "AI", "I/O", etc.
_FIRST_PERSON_I = re.compile(r"\bI\b(?!')")


def check_pronoun_violations(resume: dict) -> list[CheckResult]:
    """Flag pronouns in content fields (resume uses implied first person)."""
    results: list[CheckResult] = []

    for loc, text in _iter_content_texts(resume):
        # Case-insensitive check for most pronouns
        for m in re.finditer(r"\b(my|me|he|she|his|her|they|their|we|our)\b", text, re.IGNORECASE):
            results.append(CheckResult(
                check_name="pronoun_violation",
                severity="error",
                location=loc,
                message=f"Pronoun '{m.group()}' found — resume should use implied first person",
                offending_text=text[max(0, m.start()-20):m.end()+20],
            ))
        # Case-sensitive check for "I" (avoid flagging "AI", "I/O")
        for m in _FIRST_PERSON_I.finditer(text):
            # Skip if preceded by a letter (part of a word like "AI")
            if m.start() > 0 and text[m.start()-1].isalpha():
                continue
            # Skip if followed by a letter (part of a word)
            if m.end() < len(text) and text[m.end()].isalpha():
                continue
            results.append(CheckResult(
                check_name="pronoun_violation",
                severity="error",
                location=loc,
                message="First-person 'I' found — resume should use implied first person",
                offending_text=text[max(0, m.start()-20):m.end()+20],
            ))

    return results


# ---------------------------------------------------------------------------
# Check 3: Banned phrases
# ---------------------------------------------------------------------------

_BANNED_PHRASES = [
    r"\bleverag(ed|ing|es?)\b",
    r"\butiliz(ed|ing|es?)\b",
    r"\barchitected\b",
    r"\bproven track record\b",
    r"\bpassionate about\b",
    r"\bspearheaded?\b",
    r"\bin order to\b",
    r"\bwas responsible for\b",
    r"\bhelped develop\b",
    r"\bdrove cross-functional\b",
    r"\bensured stakeholder\b",
    r"\bresults-driven\b",
    r"\bcollaborated on the development\b",
]
_BANNED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _BANNED_PHRASES]


def check_banned_phrases(resume: dict) -> list[CheckResult]:
    """Flag banned resume-speak and filler phrases."""
    results: list[CheckResult] = []

    for loc, text in _iter_content_texts(resume):
        for pattern in _BANNED_PATTERNS:
            for m in pattern.finditer(text):
                results.append(CheckResult(
                    check_name="banned_phrase",
                    severity="warning",
                    location=loc,
                    message=f"Banned phrase '{m.group()}' found",
                    offending_text=text[max(0, m.start()-15):m.end()+15],
                ))

    return results


# ---------------------------------------------------------------------------
# Check 4: Verb repetition
# ---------------------------------------------------------------------------

def check_verb_repetition(resume: dict) -> list[CheckResult]:
    """Flag when 3+ bullets start with the same verb."""
    results: list[CheckResult] = []

    first_words: list[str] = []
    for _, text in _iter_bullet_texts(resume):
        words = text.strip().split()
        if words:
            first_words.append(words[0].lower().rstrip(",.:;"))

    from collections import Counter
    counts = Counter(first_words)
    for verb, count in counts.items():
        if count >= 3:
            results.append(CheckResult(
                check_name="verb_repetition",
                severity="warning",
                location="experience",
                message=f"Verb '{verb}' starts {count} bullets (max 2)",
                offending_text=f"{verb} x{count}",
            ))

    return results


# ---------------------------------------------------------------------------
# Check 5: Fabricated IDs
# ---------------------------------------------------------------------------

def check_fabricated_ids(resume: dict, profile_data: dict) -> list[CheckResult]:
    """Verify all accomplishment_ids and publication_ids exist in profile."""
    results: list[CheckResult] = []

    complete = profile_data.get("complete_profile", {})
    valid_acc_ids = {a.get("id") for a in complete.get("accomplishments", []) if a.get("id")}
    valid_pub_ids = {p.get("id") for p in complete.get("publications", []) if p.get("id")}

    # Check selected_research accomplishment_ids
    for i, entry in enumerate(resume.get("selected_research", [])):
        aid = entry.get("accomplishment_id")
        if aid and aid not in valid_acc_ids:
            results.append(CheckResult(
                check_name="fabricated_id",
                severity="error",
                location=f"selected_research.{i}.accomplishment_id",
                message=f"Accomplishment ID '{aid}' not found in profile",
                offending_text=aid,
            ))

    # Check experience accomplishment_ids
    for emp_key, emp_data in resume.get("experience", {}).items():
        for i, aid in enumerate(emp_data.get("accomplishment_ids", [])):
            if aid and aid not in valid_acc_ids:
                results.append(CheckResult(
                    check_name="fabricated_id",
                    severity="error",
                    location=f"experience.{emp_key}.accomplishment_ids.{i}",
                    message=f"Accomplishment ID '{aid}' not found in profile",
                    offending_text=aid,
                ))

    # Check publication_ids
    for i, pub in enumerate(resume.get("publications", [])):
        pid = pub.get("publication_id")
        if pid and pid not in valid_pub_ids:
            results.append(CheckResult(
                check_name="fabricated_id",
                severity="error",
                location=f"publications.{i}.publication_id",
                message=f"Publication ID '{pid}' not found in profile",
                offending_text=pid,
            ))

    return results


# ---------------------------------------------------------------------------
# Check 6: Schema completeness
# ---------------------------------------------------------------------------

def check_schema_completeness(resume: dict) -> list[CheckResult]:
    """Validate required fields, section sizes, and structure."""
    results: list[CheckResult] = []

    required_keys = ["tagline", "summary", "selected_research", "experience",
                     "publications", "technical_skills", "awards"]
    for key in required_keys:
        if key not in resume or resume[key] is None:
            results.append(CheckResult(
                check_name="schema",
                severity="error",
                location=key,
                message=f"Required field '{key}' is missing",
            ))

    # selected_research should have exactly 3 entries
    sr = resume.get("selected_research", [])
    if isinstance(sr, list) and len(sr) != 3:
        results.append(CheckResult(
            check_name="schema",
            severity="error" if len(sr) == 0 else "warning",
            location="selected_research",
            message=f"selected_research has {len(sr)} entries (expected 3)",
        ))

    # publications should have 4-6 entries
    pubs = resume.get("publications", [])
    if isinstance(pubs, list) and (len(pubs) < 4 or len(pubs) > 6):
        results.append(CheckResult(
            check_name="schema",
            severity="warning",
            location="publications",
            message=f"publications has {len(pubs)} entries (expected 4-6)",
        ))

    # Each experience block should have bullets
    for emp_key, emp_data in resume.get("experience", {}).items():
        if not isinstance(emp_data, dict):
            results.append(CheckResult(
                check_name="schema",
                severity="error",
                location=f"experience.{emp_key}",
                message="Experience block is not a dict",
            ))
            continue
        bullets = emp_data.get("bullets", [])
        if not bullets:
            results.append(CheckResult(
                check_name="schema",
                severity="error",
                location=f"experience.{emp_key}.bullets",
                message="No bullets found for employer",
            ))

    # technical_skills should have 3 categories
    ts = resume.get("technical_skills", {})
    if isinstance(ts, dict):
        expected_cats = {"ai_systems", "data_science", "engineering"}
        missing = expected_cats - set(ts.keys())
        for cat in missing:
            results.append(CheckResult(
                check_name="schema",
                severity="warning",
                location=f"technical_skills.{cat}",
                message=f"Technical skills category '{cat}' is missing",
            ))

    return results


# ---------------------------------------------------------------------------
# Check 7: Participial tails (fast regex pre-filter)
# ---------------------------------------------------------------------------

_PARTICIPIAL_PATTERN = re.compile(
    r",\s*(?:and\s+)?"
    r"(demonstrat(?:ing|ed|es?)|"
    r"enabl(?:ing|ed|es?)|"
    r"achiev(?:ing|ed|es?)|"
    r"result(?:ing|ed|s?) in|"
    r"ensur(?:ing|ed|es?)|"
    r"establish(?:ing|ed|es?)|"
    r"driv(?:ing|en)|"
    r"directly align(?:ing|ed|s?)|"
    r"validat(?:ing|ed|es?)|"
    r"highlight(?:ing|ed|s?)|"
    r"showcas(?:ing|ed|es?)|"
    r"illustrat(?:ing|ed|es?)|"
    r"underscor(?:ing|ed|es?)|"
    r"position(?:ing|ed|s?)|"
    r"reflect(?:ing|ed|s?))"
    r"\b",
    re.IGNORECASE,
)


def check_participial_tails_fast(resume: dict) -> list[CheckResult]:
    """Fast regex check for the most common participial tail patterns."""
    results: list[CheckResult] = []

    for loc, text in _iter_content_texts(resume):
        for m in _PARTICIPIAL_PATTERN.finditer(text):
            # Get surrounding context
            start = max(0, m.start() - 30)
            end = min(len(text), m.end() + 30)
            results.append(CheckResult(
                check_name="participial_tail",
                severity="error",
                location=loc,
                message=f"Participial tail '{m.group().strip()}' — split into separate sentence",
                offending_text=text[start:end],
            ))

    return results


# ---------------------------------------------------------------------------
# Check 8: Raw jargon metrics
# ---------------------------------------------------------------------------

_JARGON_METRICS = [
    (re.compile(r"\bCohen'?s?\s+kappa\s*[=:≈]\s*[\d.]+\b", re.IGNORECASE),
     "Cohen's kappa — use 'matched human-expert agreement' or similar"),
    (re.compile(r"\bF1\s*[=:]\s*[\d.]+\b", re.IGNORECASE),
     "Raw F1 score — contextualize or drop"),
    (re.compile(r"\bAUC\s*[=:]\s*[\d.]+\b", re.IGNORECASE),
     "Raw AUC — contextualize or drop"),
    (re.compile(r"\bBLEU\s*[=:]\s*[\d.]+\b", re.IGNORECASE),
     "Raw BLEU score — contextualize or drop"),
    (re.compile(r"\bROUGE\s*[=:]\s*[\d.]+\b", re.IGNORECASE),
     "Raw ROUGE score — contextualize or drop"),
    (re.compile(r"\bperplexity\s*[=:]\s*[\d.]+\b", re.IGNORECASE),
     "Raw perplexity — contextualize or drop"),
]


def check_raw_jargon_metrics(resume: dict) -> list[CheckResult]:
    """Flag domain-specific metrics used without plain-language context."""
    results: list[CheckResult] = []

    for loc, text in _iter_content_texts(resume):
        for pattern, advice in _JARGON_METRICS:
            for m in pattern.finditer(text):
                results.append(CheckResult(
                    check_name="raw_jargon_metric",
                    severity="warning",
                    location=loc,
                    message=f"Raw jargon metric: {advice}",
                    offending_text=text[max(0, m.start()-15):m.end()+15],
                ))

    return results


# ---------------------------------------------------------------------------
# Check 9: Critic leakage (regex pre-filter)
# ---------------------------------------------------------------------------

_LEAKAGE_PATTERNS = [
    # Gap disclaimers
    re.compile(r"\bnot\s+core\b", re.IGNORECASE),
    re.compile(r"\bbest aligned with\b.*\brather than\b", re.IGNORECASE),
    re.compile(r"\bstrongest in\b.*\bnot\b", re.IGNORECASE),
    re.compile(r"\bno direct experience\b", re.IGNORECASE),
    re.compile(r"\bgenuine gap\b", re.IGNORECASE),
    re.compile(r"\blacks\b.*\bexperience\b", re.IGNORECASE),
    re.compile(r"\bnot\s+\w+\s+experience\b", re.IGNORECASE),
    re.compile(r"\bweakest\s+in\b", re.IGNORECASE),
    re.compile(r"\bdoes not have\b", re.IGNORECASE),
    re.compile(r"\bmodel-external\b", re.IGNORECASE),
    # Strategy/positioning language (planner phrases, not resume prose)
    re.compile(r"\bbrings? strong fit for\b", re.IGNORECASE),
    re.compile(r"\bwell[- ]positioned for\b", re.IGNORECASE),
    re.compile(r"\bstrongest candidate for\b", re.IGNORECASE),
    re.compile(r"\bcentered on\b.*\b(?:evaluation|grounding|retrieval)\b", re.IGNORECASE),
]


def check_critic_leakage(resume: dict) -> list[CheckResult]:
    """Flag disclaimer/gap language that may have leaked from the critic."""
    results: list[CheckResult] = []

    # Only check content fields, not _critique, _research, or tailoring_rationale
    content_texts = []
    if resume.get("summary"):
        content_texts.append(("summary", resume["summary"]))
    content_texts.extend(_iter_bullet_texts(resume))
    content_texts.extend(_iter_description_texts(resume))

    for loc, text in content_texts:
        for pattern in _LEAKAGE_PATTERNS:
            m = pattern.search(text)
            if m:
                results.append(CheckResult(
                    check_name="critic_leakage",
                    severity="error",
                    location=loc,
                    message=f"Possible critic leakage: '{m.group()}'",
                    offending_text=text[max(0, m.start()-20):m.end()+20],
                ))

    return results


# ---------------------------------------------------------------------------
# Check 9: Redundancy (Jaccard similarity)
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "been",
    "be", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "this", "that",
    "these", "those", "it", "its", "not", "no", "nor", "so", "if", "then",
    "than", "too", "very", "just", "about", "above", "after", "before",
    "between", "into", "through", "during", "until", "while", "also",
    "built", "designed", "system", "research", "data", "using", "across",
}


def _content_words(text: str) -> set[str]:
    """Extract content words (lowercase, no stopwords)."""
    words = re.findall(r"\b\w+\b", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def check_redundancy(resume: dict) -> list[CheckResult]:
    """Flag research descriptions and bullets that overlap heavily for the same accomplishment."""
    results: list[CheckResult] = []

    # Map accomplishment_id -> research description text
    research_by_id: dict[str, tuple[str, str]] = {}
    for i, entry in enumerate(resume.get("selected_research", [])):
        aid = entry.get("accomplishment_id")
        desc = entry.get("description", "")
        if aid and desc:
            research_by_id[aid] = (f"selected_research.{i}.description", desc)

    # Check each experience bullet's accomplishment_ids for overlap.
    # Handles both old (parallel arrays) and new (bullet-as-dict) format.
    for emp_key, emp_data in resume.get("experience", {}).items():
        bullets = emp_data.get("bullets", [])
        legacy_acc_ids = emp_data.get("accomplishment_ids", [])
        for bi, bullet in enumerate(bullets):
            if isinstance(bullet, dict):
                bullet_text = bullet.get("text", "")
                bullet_aids = bullet.get("accomplishment_ids") or []
            else:
                bullet_text = bullet
                bullet_aids = []
                if bi < len(legacy_acc_ids):
                    aid = legacy_acc_ids[bi]
                    if isinstance(aid, str):
                        bullet_aids = [aid]
                    elif isinstance(aid, list):
                        bullet_aids = aid

            for aid in bullet_aids:
                if aid in research_by_id:
                    r_loc, r_desc = research_by_id[aid]
                    b_words = _content_words(bullet_text)
                    r_words = _content_words(r_desc)
                    if not b_words or not r_words:
                        continue
                    intersection = b_words & r_words
                    union = b_words | r_words
                    jaccard = len(intersection) / len(union) if union else 0

                    if jaccard > 0.4:
                        results.append(CheckResult(
                            check_name="redundancy",
                            severity="warning",
                            location=f"experience.{emp_key}.bullets.{bi} vs {r_loc}",
                            message=f"Jaccard similarity {jaccard:.2f} (>{0.4}) — "
                                    f"bullet and research description for '{aid}' overlap",
                            offending_text=f"Shared words: {', '.join(sorted(intersection)[:8])}",
                        ))

    return results


# ---------------------------------------------------------------------------
# Aggregate runner
# ---------------------------------------------------------------------------

_ALL_DETERMINISTIC_CHECKS = [
    check_word_counts,
    check_pronoun_violations,
    check_banned_phrases,
    check_verb_repetition,
    check_schema_completeness,
    check_participial_tails_fast,
    check_critic_leakage,
    check_raw_jargon_metrics,
]

_CHECKS_NEEDING_PROFILE = [
    check_fabricated_ids,
]

_CHECKS_NEEDING_RESUME_ONLY = [
    check_redundancy,
]


def run_deterministic_checks(
    resume_json: dict,
    profile_data: dict | None = None,
) -> ResumeCheckReport:
    """Run all deterministic checks on a resume. No LLM calls."""
    all_results: list[CheckResult] = []

    for check_fn in _ALL_DETERMINISTIC_CHECKS:
        all_results.extend(check_fn(resume_json))

    # Checks that only need the resume
    for check_fn in _CHECKS_NEEDING_RESUME_ONLY:
        all_results.extend(check_fn(resume_json))

    # Checks that need profile data
    if profile_data:
        for check_fn in _CHECKS_NEEDING_PROFILE:
            all_results.extend(check_fn(resume_json, profile_data))

    return ResumeCheckReport(results=all_results)
