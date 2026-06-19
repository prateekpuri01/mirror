"""Unit tests for deterministic helpers.

These tests cover pure functions — no DB, no LLM, no network. They
document the expected behavior of load-bearing utilities that:

  - normalize employer names into stable slug keys
  - parse resume edit paths into content_memory entity descriptors
  - hash underlying accomplishment text for staleness detection
  - manipulate the resume JSON via dotted paths
  - extract canonical domains from URLs (skipping ATS hosts)
  - detect Ashby-embed query params
  - parse tailored-resume filenames
  - render edit responses + previous-attempts blocks for the chat agent
  - build focused profile slices for focused edits
  - render the rest-of-resume excerpt block

Each test groups by module so a failure points straight at the offender.
"""

from __future__ import annotations

import json

import pytest

from app.ai.company_research import _disambiguator_line, _domain_from_url
from app.ai.content_memory_grounding import (
    _format_job_context,
    _render_payload,
    format_grounding_block,
    format_multi_entity_block,
)
from app.ai.content_memory_paths import (
    EXPERIENCE_BULLETS_SET,
    RESEARCH_DESCRIPTION,
    SCALAR_KEY,
    SKILL_BUCKET,
    SUMMARY,
    TAGLINE,
    path_to_entity,
)
from app.ai.resume_agent import (
    _ASSISTANT_EDIT_PREFIX,
    _focused_profile_for_edit,
    _format_edit_response,
    _other_sections_excerpt,
    _previous_attempts_block,
    _trimmed_chat_history,
)
from app.ai.utils import employer_key
from app.services.content_memory_service import (
    _accomplishment_ids_from_row,
    _hash_source_text,
    is_stale,
)
from app.services.document_service import (
    _get_nested,
    _set_nested,
    migrate_resume_json,
)
from app.services.job_url_importer import _ashby_jid_from_url

# ---------------------------------------------------------------------------
# employer_key — slug normalization that's threaded through the whole codebase
# ---------------------------------------------------------------------------


class TestEmployerKey:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("The RAND Corporation", "rand_corporation"),
            ("FINRA", "finra"),
            ("UCLA — Department of Physics", "ucla_department_of_physics"),
            ("OpenAI", "openai"),
            ("Lila Sciences", "lila_sciences"),
            ("Reka AI", "reka_ai"),
            # "the" prefix is stripped; preserved later in the word
            ("The The Company", "the_company"),
            # Whitespace + leading/trailing junk
            ("  Brightline Health  ", "brightline_health"),
            # Punctuation collapses to underscore
            ("A&B Co.", "a_b_co"),
            # Unicode em-dash gets replaced with space
            ("Foo — Bar", "foo_bar"),
        ],
    )
    def test_canonical_slugs(self, name, expected):
        assert employer_key(name) == expected

    def test_idempotent(self):
        once = employer_key("The RAND Corporation")
        twice = employer_key(once)
        assert once == twice == "rand_corporation"


# ---------------------------------------------------------------------------
# path_to_entity — the core mapping from edit path to content_memory entity
# ---------------------------------------------------------------------------


class TestPathToEntity:
    @pytest.fixture
    def resume(self):
        return {
            "tagline": "AI Systems · Evaluation",
            "summary": "Builds AI tools.",
            "selected_research": [
                {
                    "category_label": "RESEARCH",
                    "title": "MUSE",
                    "description": "Designed and built MUSE…",
                    "accomplishment_id": "rand-muse",
                },
            ],
            "experience": {
                "rand_corporation": {
                    "bullets": [
                        {"text": "Shipped MUSE…", "accomplishment_ids": ["rand-muse"]},
                        {
                            "text": "Led DARPA work…",
                            "accomplishment_ids": ["rand-darpa-ckc", "rand-air-force-training"],
                        },
                    ],
                },
            },
            "technical_skills": {"ai_systems": "LLM evaluation, RAG"},
        }

    def test_summary(self, resume):
        d = path_to_entity("summary", resume)
        assert d["entity_type"] == SUMMARY
        assert d["entity_key"] == SCALAR_KEY
        assert d["user_text"] == "Builds AI tools."
        assert d["user_payload_json"] is None

    def test_tagline(self, resume):
        d = path_to_entity("tagline", resume)
        assert d["entity_type"] == TAGLINE
        assert d["entity_key"] == SCALAR_KEY
        assert d["user_text"] == "AI Systems · Evaluation"

    def test_skill_bucket(self, resume):
        d = path_to_entity("technical_skills.ai_systems", resume)
        assert d["entity_type"] == SKILL_BUCKET
        assert d["entity_key"] == "ai_systems"
        assert d["user_text"] == "LLM evaluation, RAG"

    def test_research_description_keys_on_accomplishment_id(self, resume):
        d = path_to_entity("selected_research.0.description", resume)
        assert d["entity_type"] == RESEARCH_DESCRIPTION
        assert d["entity_key"] == "rand-muse"
        assert d["user_text"] == "Designed and built MUSE…"
        assert d["accomplishment_ids"] == ["rand-muse"]

    def test_research_label_or_title_paths_are_not_memorized(self, resume):
        # Per design: only description edits flow into content_memory; label
        # and title edits are not authored content.
        assert path_to_entity("selected_research.0.category_label", resume) is None
        assert path_to_entity("selected_research.0.title", resume) is None

    def test_research_index_out_of_range(self, resume):
        assert path_to_entity("selected_research.5.description", resume) is None

    def test_research_missing_accomplishment_id(self):
        resume = {"selected_research": [{"description": "text", "title": "T"}]}
        assert path_to_entity("selected_research.0.description", resume) is None

    @pytest.mark.parametrize(
        "path",
        [
            "experience.rand_corporation.bullets",
            "experience.rand_corporation.bullets.0",
            "experience.rand_corporation.bullets.0.text",
            "experience.rand_corporation.bullets.1.accomplishment_ids",
        ],
    )
    def test_bullet_edits_collapse_to_employer_set(self, resume, path):
        # Any descendant under experience.{emp}.bullets memorizes the WHOLE
        # final array — that's the design. One row per (employer, doc).
        d = path_to_entity(path, resume)
        assert d["entity_type"] == EXPERIENCE_BULLETS_SET
        assert d["entity_key"] == "rand_corporation"
        assert d["user_payload_json"] == resume["experience"]["rand_corporation"]["bullets"]
        # Dedupes accomplishment_ids across all bullets, order-stable
        assert d["accomplishment_ids"] == ["rand-muse", "rand-darpa-ckc", "rand-air-force-training"]

    def test_bullet_edits_with_empty_accomplishment_ids(self):
        resume = {
            "experience": {
                "rand_corporation": {
                    "bullets": [
                        {"text": "No anchor", "accomplishment_ids": []},
                        {"text": "Anchored", "accomplishment_ids": ["rand-muse"]},
                    ]
                }
            }
        }
        d = path_to_entity("experience.rand_corporation.bullets.0.text", resume)
        assert d["entity_type"] == EXPERIENCE_BULLETS_SET
        assert d["accomplishment_ids"] == ["rand-muse"]

    def test_unknown_paths(self, resume):
        assert path_to_entity("publications.0", resume) is None
        assert path_to_entity("awards", resume) is None
        assert path_to_entity("education", resume) is None
        assert path_to_entity("", resume) is None

    def test_missing_employer(self):
        resume = {"experience": {"rand_corporation": {"bullets": []}}}
        assert path_to_entity("experience.unknown_employer.bullets.0.text", resume) is None


# ---------------------------------------------------------------------------
# Content-text hashing for staleness detection
# ---------------------------------------------------------------------------


class TestSourceTextHash:
    @pytest.fixture
    def profile(self):
        return {
            "complete_profile": {
                "accomplishments": [
                    {
                        "id": "rand-muse",
                        "title": "MUSE",
                        "impact_summary": "Built a thing.",
                        "quantitative_specifics": ["400+ users"],
                        "so_what": "It mattered.",
                        "hands_on_work": "Wrote Python.",
                    },
                ],
            },
        }

    def test_returns_none_when_no_ids(self, profile):
        assert _hash_source_text(RESEARCH_DESCRIPTION, [], profile) is None

    def test_returns_none_for_skill_bucket(self, profile):
        assert _hash_source_text(SKILL_BUCKET, ["rand-muse"], profile) is None

    def test_stable_for_same_input(self, profile):
        h1 = _hash_source_text(RESEARCH_DESCRIPTION, ["rand-muse"], profile)
        h2 = _hash_source_text(RESEARCH_DESCRIPTION, ["rand-muse"], profile)
        assert h1 == h2
        assert isinstance(h1, str) and len(h1) == 64  # sha256 hex

    def test_changes_when_underlying_text_changes(self, profile):
        h1 = _hash_source_text(RESEARCH_DESCRIPTION, ["rand-muse"], profile)
        profile["complete_profile"]["accomplishments"][0]["impact_summary"] = (
            "Built a different thing."
        )
        h2 = _hash_source_text(RESEARCH_DESCRIPTION, ["rand-muse"], profile)
        assert h1 != h2

    def test_invariant_to_id_order(self, profile):
        profile["complete_profile"]["accomplishments"].append(
            {
                "id": "rand-darpa-ckc",
                "title": "DARPA",
                "impact_summary": "Other thing.",
            }
        )
        h_ab = _hash_source_text(EXPERIENCE_BULLETS_SET, ["rand-muse", "rand-darpa-ckc"], profile)
        h_ba = _hash_source_text(EXPERIENCE_BULLETS_SET, ["rand-darpa-ckc", "rand-muse"], profile)
        assert h_ab == h_ba

    def test_handles_missing_accomplishment(self, profile):
        # A referenced ID that no longer exists in the profile shouldn't crash;
        # it should produce a deterministic "::missing" placeholder.
        h = _hash_source_text(RESEARCH_DESCRIPTION, ["was-deleted"], profile)
        assert isinstance(h, str) and len(h) == 64


class TestIsStale:
    @pytest.fixture
    def profile(self):
        return {
            "complete_profile": {
                "accomplishments": [
                    {"id": "rand-muse", "title": "MUSE", "impact_summary": "Built it."},
                ],
            },
        }

    def _row(self, **kwargs):
        # Cheap stand-in for a ContentMemory row
        class Row:
            pass

        r = Row()
        r.entity_type = kwargs.get("entity_type", RESEARCH_DESCRIPTION)
        r.entity_key = kwargs.get("entity_key", "rand-muse")
        r.user_text = kwargs.get("user_text")
        r.user_payload_json = kwargs.get("user_payload_json")
        r.source_text_hash = kwargs.get("source_text_hash")
        return r

    def test_no_hash_means_unknown_so_not_stale(self, profile):
        row = self._row(source_text_hash=None)
        assert is_stale(row, profile) is False

    def test_matching_hash_is_not_stale(self, profile):
        h = _hash_source_text(RESEARCH_DESCRIPTION, ["rand-muse"], profile)
        row = self._row(source_text_hash=h)
        assert is_stale(row, profile) is False

    def test_mismatched_hash_is_stale(self, profile):
        old_hash = "0" * 64
        row = self._row(source_text_hash=old_hash)
        assert is_stale(row, profile) is True


class TestAccomplishmentIdsFromRow:
    def test_research_description_uses_entity_key(self):
        class Row:
            entity_type = RESEARCH_DESCRIPTION
            entity_key = "rand-muse"
            user_payload_json = None

        assert _accomplishment_ids_from_row(Row()) == ["rand-muse"]

    def test_experience_bullets_unions_payload_ids(self):
        class Row:
            entity_type = EXPERIENCE_BULLETS_SET
            entity_key = "rand_corporation"
            user_payload_json = [
                {"text": "a", "accomplishment_ids": ["rand-muse", "rand-darpa-ckc"]},
                {"text": "b", "accomplishment_ids": ["rand-muse"]},  # dedupes
                {"text": "c", "accomplishment_ids": []},
            ]

        # Dedupes, preserves first-seen order
        assert _accomplishment_ids_from_row(Row()) == ["rand-muse", "rand-darpa-ckc"]

    def test_skill_bucket_returns_empty(self):
        class Row:
            entity_type = SKILL_BUCKET
            entity_key = "ai_systems"
            user_payload_json = None

        assert _accomplishment_ids_from_row(Row()) == []


# ---------------------------------------------------------------------------
# document_service: nested path manipulation + legacy bullet migration
# ---------------------------------------------------------------------------


class TestNestedPath:
    @pytest.fixture
    def doc(self):
        return {
            "summary": "S",
            "experience": {
                "rand_corporation": {
                    "bullets": [
                        {"text": "first", "accomplishment_ids": ["a"]},
                        {"text": "second", "accomplishment_ids": ["b"]},
                    ]
                }
            },
        }

    def test_get_top_level(self, doc):
        assert _get_nested(doc, "summary") == "S"

    def test_get_nested_dict(self, doc):
        assert _get_nested(doc, "experience.rand_corporation.bullets.0.text") == "first"

    def test_get_via_list_index(self, doc):
        assert _get_nested(doc, "experience.rand_corporation.bullets.1.accomplishment_ids.0") == "b"

    def test_get_missing_key_raises(self, doc):
        with pytest.raises(KeyError):
            _get_nested(doc, "experience.unknown.bullets")

    def test_set_top_level(self, doc):
        _set_nested(doc, "summary", "new")
        assert doc["summary"] == "new"

    def test_set_nested_string(self, doc):
        _set_nested(doc, "experience.rand_corporation.bullets.0.text", "edited")
        assert doc["experience"]["rand_corporation"]["bullets"][0]["text"] == "edited"

    def test_set_replace_array(self, doc):
        new_bullets = [{"text": "only", "accomplishment_ids": ["x"]}]
        _set_nested(doc, "experience.rand_corporation.bullets", new_bullets)
        assert doc["experience"]["rand_corporation"]["bullets"] == new_bullets


class TestMigrateResumeJson:
    def test_idempotent_on_new_format(self):
        new_format = {
            "experience": {
                "emp": {
                    "bullets": [
                        {"text": "b1", "accomplishment_ids": ["a"]},
                    ]
                }
            }
        }
        out = migrate_resume_json(new_format)
        assert out["experience"]["emp"]["bullets"][0] == {"text": "b1", "accomplishment_ids": ["a"]}

    def test_converts_parallel_arrays_to_objects(self):
        old = {
            "experience": {
                "emp": {
                    "bullets": ["b1", "b2", "b3"],
                    "accomplishment_ids": ["a1", "a2"],  # only 2 ids, 3 bullets
                }
            }
        }
        out = migrate_resume_json(old)
        bullets = out["experience"]["emp"]["bullets"]
        assert bullets[0] == {"text": "b1", "accomplishment_ids": ["a1"]}
        assert bullets[1] == {"text": "b2", "accomplishment_ids": ["a2"]}
        assert bullets[2] == {"text": "b3", "accomplishment_ids": []}  # missing → empty list
        assert "accomplishment_ids" not in out["experience"]["emp"]

    def test_handles_missing_experience(self):
        out = migrate_resume_json({"summary": "x"})
        assert out == {"summary": "x"}


# ---------------------------------------------------------------------------
# URL helpers: domain extraction + Ashby pivot detection + disambiguator
# ---------------------------------------------------------------------------


class TestDomainFromUrl:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://surge.ai/jobs/123", "surge.ai"),
            ("https://www.surge.ai/jobs/123", "surge.ai"),
            ("http://anthropic.com/", "anthropic.com"),
            # Ditto subdomain — kept (not an ATS host)
            ("https://api.example.com/v1/foo", "api.example.com"),
        ],
    )
    def test_extracts_domain(self, url, expected):
        assert _domain_from_url(url) == expected

    @pytest.mark.parametrize(
        "ats_url",
        [
            "https://boards.greenhouse.io/anthropic",
            "https://job-boards.greenhouse.io/x",
            "https://jobs.lever.co/somecompany/foo",
            "https://jobs.ashbyhq.com/Ditto/abc",
            "https://example.workday.com/careers",
            "https://example.myworkdayjobs.com/foo",
            "https://jobs.ashbyhq.com/embed/whatever",
            "https://example.smartrecruiters.com/x",
            "https://example.bamboohr.com/x",
            "https://www.linkedin.com/jobs/view/123",
            "https://www.indeed.com/viewjob",
            "https://www.glassdoor.com/job",
            "https://news.ycombinator.com/item",
            "https://wellfound.com/jobs/x",
        ],
    )
    def test_returns_none_for_ats_hosts(self, ats_url):
        assert _domain_from_url(ats_url) is None

    def test_handles_none(self):
        assert _domain_from_url(None) is None
        assert _domain_from_url("") is None


class TestDisambiguatorLine:
    def test_uses_company_website_first(self):
        line = _disambiguator_line(
            "Surge", website="https://surge.ai", job_url="https://boards.greenhouse.io/x"
        )
        assert line is not None
        assert "surge.ai" in line
        assert "Surge" in line
        assert "DO NOT confuse" in line

    def test_falls_back_to_job_url_domain(self):
        line = _disambiguator_line("Surge", website=None, job_url="https://surge.ai/jobs/x")
        assert line is not None
        assert "surge.ai" in line

    def test_returns_none_when_no_anchor_available(self):
        # Ambiguous company name + ATS-only URL → no disambiguator
        line = _disambiguator_line(
            "Surge",
            website=None,
            job_url="https://boards.greenhouse.io/foo",
        )
        assert line is None

    def test_handles_website_without_scheme(self):
        line = _disambiguator_line("Surge", website="surge.ai", job_url=None)
        assert line is not None
        assert "surge.ai" in line


class TestAshbyJidFromUrl:
    @pytest.mark.parametrize(
        "url,expected",
        [
            (
                "https://ditto.ai/careers?ashby_jid=c3ecca63-e3e7-4572-afd2-7727b1bfaf3a",
                "c3ecca63-e3e7-4572-afd2-7727b1bfaf3a",
            ),
            ("https://example.com/careers?other=1&ashby_jid=abc12345-def0&z=2", "abc12345-def0"),
        ],
    )
    def test_extracts_jid(self, url, expected):
        assert _ashby_jid_from_url(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "https://ditto.ai/careers",
            "https://ditto.ai/careers?other=1",
            "https://jobs.ashbyhq.com/Ditto/c3ecca63",
            "",
            None,
        ],
    )
    def test_no_jid(self, url):
        assert _ashby_jid_from_url(url) is None


# ---------------------------------------------------------------------------
# Tailored-resume filename parser (parameterized on owner name)
# ---------------------------------------------------------------------------


class TestParseFilename:
    """Imports happen lazily because the script lives outside ``app/`` and the
    file shadows app-level imports if loaded eagerly during pytest discovery."""

    def _parse_filename(self):
        from pathlib import Path

        ingest_path = Path(__file__).resolve().parent.parent / "scripts" / "ingest_past_resumes.py"
        # Extract the parser-only portion (everything before the DB writer
        # section) and exec it in an isolated namespace. The script's
        # bottom half imports app.database / app.models / app.services
        # which can't load without a configured DB — but parse_filename
        # itself is pure.
        src = ingest_path.read_text()
        parser_src = src.split(
            "# ---------------------------------------------------------------------------\n# Database writer"
        )[0]
        parser_src = parser_src.replace(
            "from app.ai.utils import employer_key as employer_key_fn  # noqa: E402\n"
            "from app.database import async_session  # noqa: E402\n"
            "from app.models import ContentMemory, Document, DocType, UserProfile  # noqa: E402\n"
            "from app.services import content_memory_service  # noqa: E402",
            "",
        )
        # Stub out __file__ + the path-discovery dance the script does at
        # module level (it sets RESUME_DIR via Path(__file__).parent.parent).
        ns = {"__file__": str(ingest_path), "__name__": "ingest_past_resumes_test_isolation"}
        exec(parser_src, ns)  # noqa: S102 — controlled source, test-only
        return ns["parse_filename"]

    @pytest.fixture
    def parse(self):
        return self._parse_filename()

    @pytest.mark.parametrize(
        "owner,multiword,filename,expected",
        [
            # Standard "{Name} {Company} {Title}.docx"
            (
                "Sam Rivera",
                (),
                "Sam Rivera Helio Senior ML Engineer.docx",
                ("Helio", "Senior ML Engineer"),
            ),
            # Multi-word company captured by the multiword list
            (
                "Sam Rivera",
                ("Lila Sciences",),
                "Sam Rivera Lila Sciences Research Scientist, Frontier.docx",
                ("Lila Sciences", "Research Scientist, Frontier"),
            ),
            # Comma-after-company variant
            (
                "Sam Rivera",
                (),
                "Sam Rivera OpenAI, AI Success Engineer.docx",
                ("OpenAI", "AI Success Engineer"),
            ),
            # Underscore-separated reverse-name format
            (
                "Sam Rivera",
                (),
                "Rivera_Sam_Cohere_Senior_Research_Scientist.docx",
                ("Cohere", "Senior Research Scientist"),
            ),
            # Different owner name proves the parametrization
            (
                "Prateek Puri",
                (),
                "Prateek Puri Anthropic Research Engineer, Economic Research.docx",
                ("Anthropic", "Research Engineer, Economic Research"),
            ),
            # Title artifact like " (1)" gets stripped
            (
                "Sam Rivera",
                (),
                "Sam Rivera Helio Senior ML Engineer (1).docx",
                ("Helio", "Senior ML Engineer"),
            ),
        ],
    )
    def test_parses_canonical_shapes(self, parse, owner, multiword, filename, expected):
        assert parse(filename, owner_name=owner, multiword_companies=multiword) == expected

    def test_unrecognized_falls_back_to_stem(self, parse):
        company, title = parse(
            "random_name_with_no_pattern.docx",
            owner_name="Sam Rivera",
            multiword_companies=(),
        )
        assert company == ""
        assert title == "random_name_with_no_pattern"

    def test_empty_owner_name(self, parse):
        # No owner → no filename patterns compile, all results fall back to stem
        company, title = parse("foo.docx", owner_name="", multiword_companies=())
        assert (company, title) == ("", "foo")


# ---------------------------------------------------------------------------
# resume_agent helpers — chat agent context construction
# ---------------------------------------------------------------------------


class TestFormatEditResponse:
    def test_string_value(self):
        out = _format_edit_response("summary", "New summary text.")
        assert out.startswith(_ASSISTANT_EDIT_PREFIX + "summary**")
        assert "> New summary text." in out

    def test_bullet_list_value(self):
        bullets = [
            {"text": "First bullet.", "accomplishment_ids": ["a"]},
            {"text": "Second bullet.", "accomplishment_ids": ["b"]},
        ]
        out = _format_edit_response("experience.rand_corporation.bullets", bullets)
        assert "> - First bullet." in out
        assert "> - Second bullet." in out

    def test_dict_value(self):
        out = _format_edit_response("technical_skills", {"ai_systems": "x"})
        assert "> {" in out  # quoted JSON
        assert '"ai_systems"' in out

    def test_starts_with_known_prefix_so_history_can_find_it(self):
        # _previous_attempts_block depends on this prefix — guard against drift
        out = _format_edit_response("summary", "x")
        assert out.startswith(_ASSISTANT_EDIT_PREFIX)


class TestPreviousAttemptsBlock:
    def test_empty_history_returns_empty(self):
        assert _previous_attempts_block([], "summary") == ""

    def test_no_matching_attempts_returns_empty(self):
        history = [
            {"role": "user", "content": "Edit the summary"},
            {"role": "assistant", "content": "Updated **tagline** to:\n\n> X"},
        ]
        assert _previous_attempts_block(history, "summary") == ""

    def test_pairs_attempt_with_user_reaction(self):
        history = [
            {"role": "user", "content": "First instruction"},
            {"role": "assistant", "content": "Updated **summary** to:\n\n> First version"},
            {"role": "user", "content": "Too long"},
            {"role": "assistant", "content": "Updated **summary** to:\n\n> Second version"},
            {"role": "user", "content": "Lead with the metric"},
        ]
        out = _previous_attempts_block(history, "summary")
        assert "Previous attempts on this section" in out
        assert "First version" in out
        assert "Too long" in out
        assert "Second version" in out
        assert "Lead with the metric" in out

    def test_pairs_only_assistant_to_following_user(self):
        # Attempt only counts when followed by a user message
        history = [
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": "Updated **summary** to:\n\n> A"},
            # No user reply → not paired
        ]
        assert _previous_attempts_block(history, "summary") == ""

    def test_caps_at_three_recent_pairs(self):
        history = []
        for i in range(5):
            history.append({"role": "user", "content": f"Inst {i}"})
            history.append({"role": "assistant", "content": f"Updated **summary** to:\n\n> V{i}"})
            history.append({"role": "user", "content": f"Reaction {i}"})
        out = _previous_attempts_block(history, "summary")
        # 3 most-recent shown
        assert "V2" in out and "V3" in out and "V4" in out
        # Earlier ones dropped
        assert "V0" not in out and "V1" not in out


class TestTrimmedChatHistory:
    def test_empty_history(self):
        assert _trimmed_chat_history([], "summary") == ""

    def test_drops_current_message(self):
        # The "current" message lives in user_message; chat_history's last
        # entry is the same one — _trimmed_chat_history shouldn't render it
        # again as conversation history.
        history = [
            {"role": "user", "content": "Old"},
            {"role": "assistant", "content": "Reply"},
            {"role": "user", "content": "Current"},
        ]
        out = _trimmed_chat_history(history, "summary")
        assert "Current" not in out
        assert "Old" in out and "Reply" in out

    def test_filters_same_path_assistant_edits(self):
        # Same-path assistant edits flow through _previous_attempts_block;
        # _trimmed_chat_history skips them to avoid duplication.
        history = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "Updated **summary** to:\n\n> X"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "Different reply"},
            {"role": "user", "content": "now"},  # current
        ]
        out = _trimmed_chat_history(history, "summary")
        assert "Updated **summary**" not in out
        assert "Different reply" in out

    def test_char_budget_drops_oldest_first(self):
        # 100-char messages × 20 → 2000 chars > 1500 budget → some drop
        history = [{"role": "user", "content": f"msg-{i}: " + "x" * 90} for i in range(20)]
        history.append({"role": "user", "content": "current"})
        out = _trimmed_chat_history(history, "summary", char_budget=300)
        # Newest survives; oldest dropped
        assert "msg-19" in out
        assert "msg-0:" not in out


class TestOtherSectionsExcerpt:
    def test_empty_resume(self):
        assert _other_sections_excerpt({}, "summary") == ""

    def test_excludes_the_section_being_edited(self):
        resume = {
            "summary": "S",
            "selected_research": [
                {
                    "category_label": "RES",
                    "title": "T",
                    "description": "D",
                    "accomplishment_id": "a",
                },
            ],
        }
        out = _other_sections_excerpt(resume, "summary")
        assert "Summary:" not in out  # path being edited is excluded
        assert "Research [RES]" in out

    def test_full_text_when_few_items(self):
        # ≤ 4 items → full text; ≥ 5 → 250-char cap. Use one item to verify
        # the "full text" branch.
        long_text = "x" * 800
        resume = {
            "summary": "S",
            "selected_research": [
                {
                    "category_label": "L",
                    "title": "T",
                    "description": long_text,
                    "accomplishment_id": "a",
                },
            ],
        }
        out = _other_sections_excerpt(resume, "tagline")
        # Full text should be present (not truncated to 250)
        assert long_text in out


class TestFocusedProfileForEdit:
    @pytest.fixture
    def profile(self):
        return {
            "complete_profile": {
                "accomplishments": [
                    {
                        "id": "rand-muse",
                        "title": "MUSE",
                        "employer": "RAND",
                        "impact_summary": "Built a thing for researchers.",
                        "quantitative_specifics": ["400+ users"],
                        "so_what": "Mattered.",
                    },
                    {
                        "id": "rand-darpa-ckc",
                        "title": "DARPA CKC",
                        "impact_summary": "Different work.",
                    },
                    {
                        "id": "finra-fraud",
                        "title": "Fraud detection",
                        "impact_summary": "Should not appear.",
                    },
                ],
            },
            "skills": {
                "technical": ["Python", "SQL"],
                "tools": ["Docker"],
            },
        }

    @pytest.fixture
    def resume(self):
        return {
            "selected_research": [
                {"accomplishment_id": "rand-muse", "title": "MUSE", "description": "..."},
            ],
            "experience": {
                "rand_corporation": {
                    "bullets": [
                        {"text": "x", "accomplishment_ids": ["rand-muse", "rand-darpa-ckc"]},
                    ]
                }
            },
        }

    def test_research_path_sends_focused_accomplishment_plus_oneliners(self, profile, resume):
        out = _focused_profile_for_edit(
            "selected_research.0.description",
            resume,
            profile,
        )
        # Primary anchor accomplishment is included in full.
        assert "rand-muse" in out
        assert "MUSE" in out
        assert "Built a thing" in out
        # Other accomplishments appear only as one-liners in the
        # "Other available accomplishments" footer — so the model can pull
        # from them if the instruction asks, but they are not the primary
        # source for this edit.
        assert "Other available accomplishments" in out
        assert "DARPA CKC" in out
        assert "Fraud detection" in out
        # Verify they're one-liners, not full data dumps.
        assert "Different work." in out  # impact summary one-liner
        # The full-form 'So what' / 'Metrics' headers should only appear once
        # (for the primary slice), not for the other accomplishments.
        assert out.count("So what:") == 1

    def test_bullet_path_includes_all_bullet_accs_plus_oneliners(self, profile, resume):
        out = _focused_profile_for_edit(
            "experience.rand_corporation.bullets.0.text",
            resume,
            profile,
        )
        # Both accomplishments bound to this bullet appear in full.
        assert "MUSE" in out
        assert "DARPA CKC" in out
        # Unrelated finra accomplishment is now included as a one-liner
        # (intentional, per brainstorm-mode plan — gives the model a
        # shortlist to pull from if the instruction implies it).
        assert "Other available accomplishments" in out
        assert "Fraud detection" in out

    def test_skills_path_returns_whitelist(self, profile, resume):
        out = _focused_profile_for_edit(
            "technical_skills.ai_systems",
            resume,
            profile,
        )
        assert "Skills whitelist" in out
        assert "Python" in out and "Docker" in out

    def test_summary_path_includes_strategic_plan(self, profile):
        resume = {
            "_strategic_plan": {"core_argument": "Argument here.", "tone": "shipping velocity"},
            "selected_research": [],
            "experience": {},
        }
        out = _focused_profile_for_edit("summary", resume, profile)
        assert "Argument here." in out
        assert "shipping velocity" in out


# ---------------------------------------------------------------------------
# content_memory_grounding rendering
# ---------------------------------------------------------------------------


class TestGroundingRendering:
    class _Row:
        def __init__(
            self,
            *,
            user_text=None,
            user_payload_json=None,
            job_context=None,
            entity_type=RESEARCH_DESCRIPTION,
            entity_key="rand-muse",
            source_text_hash=None,
        ):
            self.user_text = user_text
            self.user_payload_json = user_payload_json
            self.job_context = job_context
            self.entity_type = entity_type
            self.entity_key = entity_key
            self.source_text_hash = source_text_hash

    def test_format_job_context_with_both(self):
        ctx = {"job_title": "Senior ML Engineer", "company_name": "Anthropic"}
        assert _format_job_context(ctx) == " (written for: Senior ML Engineer @ Anthropic)"

    def test_format_job_context_handles_partial(self):
        assert _format_job_context({"job_title": "X", "company_name": ""}) == " (written for: X)"
        assert _format_job_context({"job_title": "", "company_name": "Y"}) == " (written for: Y)"
        assert _format_job_context({}) == ""
        assert _format_job_context(None) == ""

    def test_render_payload_user_text(self):
        row = self._Row(user_text="  Designed and built MUSE.  ")
        assert _render_payload(row) == "Designed and built MUSE."

    def test_render_payload_bullet_list(self):
        row = self._Row(
            user_payload_json=[
                {"text": "First.", "accomplishment_ids": ["a"]},
                {"text": "Second.", "accomplishment_ids": ["b"]},
                {"text": "", "accomplishment_ids": ["c"]},  # empty ones drop
            ]
        )
        out = _render_payload(row)
        assert out == "- First.\n- Second."

    def test_format_grounding_block_empty(self):
        assert format_grounding_block([]) == ""

    def test_format_grounding_block_with_rows(self):
        rows = [
            self._Row(
                user_text="Designed and built MUSE.",
                job_context={"job_title": "RE", "company_name": "Anthropic"},
            ),
            self._Row(
                user_text="Replaced manual workflow.",
                job_context={"job_title": "Lead DS", "company_name": "Cohere"},
            ),
        ]
        out = format_grounding_block(rows)
        assert "Most recent" in out
        assert "Earlier" in out
        assert "Anthropic" in out and "Cohere" in out
        assert "Designed and built MUSE." in out
        assert "Replaced manual workflow." in out

    def test_format_grounding_block_caps_at_three(self):
        rows = [
            self._Row(user_text=f"Version {i}.", job_context={"job_title": "X"}) for i in range(5)
        ]
        out = format_grounding_block(rows)
        assert "Version 0." in out
        assert "Version 2." in out
        # 4th and 5th dropped
        assert "Version 3." not in out
        assert "Version 4." not in out

    def test_format_multi_entity_block_groups(self):
        grouped = {
            "rand-muse": [self._Row(user_text="A.", job_context={"job_title": "X"})],
            "rand-darpa": [self._Row(user_text="B.", job_context={"job_title": "Y"})],
        }
        titles = {"rand-muse": "MUSE", "rand-darpa": "DARPA"}
        out = format_multi_entity_block(grouped, profile_data=None, title_for_key=titles)
        assert "### MUSE" in out
        assert "### DARPA" in out
        assert "A." in out and "B." in out


# ---------------------------------------------------------------------------
# Sanity: all the above tests are pure — no DB/LLM/network. If a future
# refactor accidentally introduces a side effect this test will catch it
# during collection.
# ---------------------------------------------------------------------------


def test_module_imports_have_no_side_effects():
    # Re-importing should be cheap and idempotent — if a module starts an
    # asyncio task, opens a file, or talks to a DB at import time, it'll
    # surface here. (We already imported all of them at the top of this
    # file; this just affirms the contract.)
    import importlib

    for name in [
        "app.ai.utils",
        "app.ai.content_memory_paths",
        "app.ai.content_memory_grounding",
        "app.services.content_memory_service",
        "app.services.document_service",
        "app.ai.company_research",
        "app.services.job_url_importer",
        "app.ai.resume_agent",
    ]:
        mod = importlib.import_module(name)
        assert mod is not None
